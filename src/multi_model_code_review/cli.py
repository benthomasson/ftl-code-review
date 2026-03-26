"""Command-line interface for multi-model code review."""

import asyncio
import sys

import click

import json

from . import Verdict
from .aggregator import aggregate_reviews
from .git_utils import extract_changed_files, fetch_pr_locally, get_diff, get_github_issue, get_pr_diff, post_pr_comment, pr_output_dir_name, read_file_content
from .lint import check_test_discoverability, get_changed_python_files, run_lint_checks, run_lint_fixes
from .fixer import fix_blocks as fix_blocks_async
from .observations import coverage_map_tests, file_imports, format_test_results, gather_function_context, gather_related_test_files, run_observations, run_tests_for_files
from .prompts import build_observe_prompt, build_review_prompt, build_spec_check_prompt
from .report import format_aggregate_review, format_summary
from .reviewer import (
    observe_with_model,
    parse_observe_response,
    preflight_check,
    review_with_model,
    review_with_models,
    run_model,
)

DEFAULT_MODELS = ["claude", "gemini"]


async def _gather_coverage_lookups(python_files: list[str], repo_path: str, limit: int = 10) -> dict:
    """Gather coverage-map lookups for multiple files in parallel.

    Returns dict mapping observation names to results.
    """
    files_to_check = python_files[:limit]
    if not files_to_check:
        return {}

    tasks = [coverage_map_tests(f, repo_path) for f in files_to_check]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    observations = {}
    for file_path, result in zip(files_to_check, results):
        if isinstance(result, Exception):
            continue
        if "error" not in result and result.get("tests"):
            obs_name = f"coverage_{file_path.replace('/', '_').replace('.py', '')}"
            observations[obs_name] = result

    return observations


@click.group()
@click.version_option()
def cli():
    """Multi-model code review - AI-first review using multiple models."""
    pass


@cli.command()
@click.option(
    "--branch",
    "-b",
    default=None,
    help="Branch to review (default: staged changes)",
)
@click.option(
    "--base",
    default="main",
    help="Base branch to diff against (default: main)",
)
@click.option(
    "--pr",
    default=None,
    help="GitHub PR to review (URL, owner/repo#N, or number)",
)
@click.option(
    "--repo",
    "-r",
    type=click.Path(exists=True, file_okay=False, dir_okay=True),
    default=".",
    help="Repository directory to analyze (default: current directory)",
)
@click.option(
    "--spec",
    "-s",
    default=None,
    help="Path to spec file for compliance checking",
)
@click.option(
    "--model",
    "-m",
    multiple=True,
    help="Models to use (default: claude, gemini)",
)
@click.option(
    "--output",
    "-o",
    type=click.Choice(["full", "summary"]),
    default="full",
    help="Output format (default: full)",
)
@click.option(
    "--output-dir",
    "-d",
    type=click.Path(),
    default=None,
    help="Directory to save outputs (report.md + per-model raw responses)",
)
@click.option(
    "--lint/--no-lint",
    default=False,
    help="Run lint checks (black, isort, ruff) before model review",
)
@click.option(
    "--fix-lint",
    is_flag=True,
    default=False,
    help="Auto-fix lint issues before model review",
)
@click.option(
    "--observations",
    type=click.Path(exists=True),
    default=None,
    help="JSON file with observation results (from 'observe' command)",
)
@click.option(
    "--beliefs",
    type=click.Path(exists=True),
    default=None,
    help="Path to beliefs.md file for belief-aware review (from code-expert)",
)
@click.option(
    "--issue",
    type=click.Path(exists=True),
    default=None,
    help="Path to issue description file to check changes against",
)
@click.option(
    "--github-issue",
    default=None,
    help="GitHub issue to check changes against (URL, owner/repo#N, or number)",
)
@click.option(
    "--comment",
    is_flag=True,
    default=False,
    help="Post review as a comment on the PR (requires --pr)",
)
def review(branch, base, pr, repo, spec, model, output, output_dir, lint, fix_lint, observations, beliefs, issue, github_issue, comment):
    """Run code review with multiple models."""
    import os

    if comment and not pr:
        click.echo("Error: --comment requires --pr", err=True)
        sys.exit(1)

    models = list(model) if model else DEFAULT_MODELS

    # Generate default output_dir if not specified
    if output_dir is None:
        if pr:
            branch_id = pr_output_dir_name(pr)
        else:
            branch_id = (branch or "staged").replace("/", "-")
        output_dir = os.path.join("reviews", branch_id)
        click.echo(f"Saving review to {output_dir}/", err=True)

    # Lint fix/check (pre-model gate) — skip for PR reviews (remote repo)
    if (fix_lint or lint) and not pr:
        py_files = get_changed_python_files(branch, base, cwd=repo)
        if py_files:
            if fix_lint:
                click.echo(f"Fixing lint issues on {len(py_files)} files...", err=True)
                fix_result = run_lint_fixes(py_files, cwd=repo)
                if fix_result.total_fixed > 0:
                    click.echo(fix_result.summary, err=True)

            click.echo(f"Running lint checks on {len(py_files)} files...", err=True)
            lint_result = run_lint_checks(py_files, cwd=repo)
            if not lint_result.passed:
                click.echo("Lint checks failed:", err=True)
                click.echo(lint_result.summary, err=True)
                sys.exit(2)
            click.echo("Lint checks passed", err=True)

    # Preflight check
    missing = preflight_check(models)
    if missing:
        click.echo(f"Error: Missing CLI tools: {', '.join(missing)}", err=True)
        click.echo("Install the missing tools or use --model to select available ones.", err=True)
        sys.exit(1)

    # Get diff
    try:
        if pr and repo != ".":
            # Local repo available — fetch PR branch for full context + observations
            click.echo(f"Fetching PR branch into {repo}...", err=True)
            head_branch, base_branch, diff_ref = fetch_pr_locally(pr, repo)
            diff_content = get_diff(head_branch, base_branch, cwd=repo)
        elif pr:
            diff_content, diff_ref, _ = get_pr_diff(pr)
        elif branch:
            diff_ref = branch
            diff_content = get_diff(branch, base, cwd=repo)
        else:
            diff_ref = "staged"
            diff_content = get_diff(cwd=repo)
    except (RuntimeError, ValueError) as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)

    if not diff_content.strip():
        click.echo("No changes to review.", err=True)
        sys.exit(0)

    # Read spec if provided
    spec_content = None
    if spec:
        spec_content = read_file_content(spec)
        if spec_content is None:
            click.echo(f"Warning: Spec file not found: {spec}", err=True)

    # Load observations if provided
    obs_data = None
    if observations:
        with open(observations) as f:
            obs_data = json.load(f)
        click.echo(f"Loaded {len(obs_data)} observation(s) from {observations}", err=True)

    # Load beliefs if provided
    beliefs_content = None
    if beliefs:
        beliefs_content = read_file_content(beliefs)
        if beliefs_content:
            click.echo(f"Loaded beliefs from {beliefs}", err=True)
        else:
            click.echo(f"Warning: Could not read beliefs file: {beliefs}", err=True)

    # Load issue if provided
    issue_content = None
    if github_issue:
        try:
            issue_content = get_github_issue(github_issue)
            click.echo(f"Fetched GitHub issue {github_issue}", err=True)
        except (RuntimeError, ValueError) as e:
            click.echo(f"Warning: Could not fetch GitHub issue: {e}", err=True)
    elif issue:
        issue_content = read_file_content(issue)
        if issue_content:
            click.echo(f"Loaded issue from {issue}", err=True)
        else:
            click.echo(f"Warning: Could not read issue file: {issue}", err=True)

    # Build prompt
    prompt = build_review_prompt(diff_content, spec_content, observations=obs_data, beliefs_content=beliefs_content, issue_content=issue_content)

    # Run reviews
    click.echo(f"Running review with {', '.join(models)}...", err=True)
    reviews = asyncio.run(review_with_models(models, prompt, observations=obs_data))

    # Aggregate
    result = aggregate_reviews(diff_ref, reviews, spec)

    # Format output
    if output == "full":
        report = format_aggregate_review(result)
    else:
        report = format_summary(result)

    # Save outputs
    os.makedirs(output_dir, exist_ok=True)

    report_path = os.path.join(output_dir, "report.md")
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(report)
    click.echo(f"Saved report to {report_path}", err=True)

    for model_review in reviews:
        raw_path = os.path.join(output_dir, f"{model_review.model}-raw.txt")
        with open(raw_path, "w", encoding="utf-8") as f:
            f.write(model_review.raw_response)
        click.echo(f"Saved {model_review.model} raw response to {raw_path}", err=True)

    # Always output to stdout
    click.echo(report)

    # Post as PR comment if requested
    if comment:
        try:
            post_pr_comment(pr, report)
            click.echo(f"Posted review as comment on {diff_ref}", err=True)
        except RuntimeError as e:
            click.echo(f"Error posting comment: {e}", err=True)
            sys.exit(1)


@cli.command()
@click.option(
    "--branch",
    "-b",
    default=None,
    help="Branch to analyze (default: staged changes)",
)
@click.option(
    "--base",
    default="main",
    help="Base branch to diff against (default: main)",
)
@click.option(
    "--repo",
    "-r",
    type=click.Path(exists=True, file_okay=False, dir_okay=True),
    default=".",
    help="Repository directory to analyze (default: current directory)",
)
@click.option(
    "--model",
    "-m",
    default="claude",
    help="Model to use for observation gathering (default: claude)",
)
@click.option(
    "--output",
    "-o",
    type=click.Path(),
    default=None,
    help="Output file for observations JSON (default: stdout)",
)
@click.option(
    "--run/--no-run",
    default=True,
    help="Run observations after gathering (default: --run)",
)
def observe(branch, base, repo, model, output, run):
    """
    Gather observations needed for code review.

    Analyzes the diff and determines what additional information is needed,
    then optionally runs the observations. Output can be passed to 'review --observations'.

    Example workflow:
        code-review observe -b feature-branch -o obs.json
        code-review review -b feature-branch --observations obs.json
    """
    # Get diff
    try:
        if branch:
            diff_ref = branch
            diff_content = get_diff(branch, base, cwd=repo)
        else:
            diff_ref = "staged"
            diff_content = get_diff(cwd=repo)
    except RuntimeError as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)

    if not diff_content.strip():
        click.echo("No changes to analyze.", err=True)
        sys.exit(0)

    # Preflight check
    if preflight_check([model]) != []:
        click.echo(f"Error: Model '{model}' CLI not available", err=True)
        sys.exit(1)

    # Run observation gathering
    click.echo(f"Gathering observations with {model}...", err=True)
    requested_obs = asyncio.run(observe_with_model(model, diff_content))

    if not requested_obs:
        click.echo("No observations needed.", err=True)
        result = {}
    else:
        click.echo(f"Model requested {len(requested_obs)} observation(s):", err=True)
        for obs in requested_obs:
            click.echo(f"  - {obs.get('tool')}: {obs.get('name')}", err=True)

        if run:
            # Run the observations
            click.echo("Running observations...", err=True)
            result = asyncio.run(run_observations(requested_obs, repo))

            # Flag any failed observations
            failed_obs = [(name, res) for name, res in result.items() if isinstance(res, dict) and "error" in res]
            if failed_obs:
                click.echo(click.style(f"WARNING: {len(failed_obs)} observation(s) failed:", fg="yellow"), err=True)
                for name, res in failed_obs:
                    click.echo(click.style(f"  - {name}: {res.get('error', 'Unknown error')}", fg="yellow"), err=True)

            succeeded = len(result) - len(failed_obs)
            click.echo(f"Completed {len(result)} observation(s) ({succeeded} succeeded, {len(failed_obs)} failed)", err=True)
        else:
            # Just output the requests (--no-run)
            result = {"_requests": requested_obs, "_results": {}}

    # Output
    output_json = json.dumps(result, indent=2, default=str)
    if output:
        with open(output, "w") as f:
            f.write(output_json)
        click.echo(f"Saved observations to {output}", err=True)
    else:
        click.echo(output_json)


@cli.command()
@click.option(
    "--branch",
    "-b",
    default=None,
    help="Branch to review (default: staged changes)",
)
@click.option(
    "--base",
    default="main",
    help="Base branch to diff against (default: main)",
)
@click.option(
    "--pr",
    default=None,
    help="GitHub PR to review (URL, owner/repo#N, or number)",
)
@click.option(
    "--repo",
    "-r",
    type=click.Path(exists=True, file_okay=False, dir_okay=True),
    default=".",
    help="Repository directory to analyze (default: current directory)",
)
@click.option(
    "--spec",
    "-s",
    default=None,
    help="Path to spec file for compliance checking",
)
@click.option(
    "--model",
    "-m",
    multiple=True,
    help="Models to use (default: claude, gemini)",
)
@click.option(
    "--output-dir",
    "-d",
    type=click.Path(),
    default=None,
    help="Directory to save outputs (report.md + per-model raw responses)",
)
@click.option(
    "--lint/--no-lint",
    default=False,
    help="Run lint checks (black, isort, ruff) before model review",
)
@click.option(
    "--fix-lint",
    is_flag=True,
    default=False,
    help="Auto-fix lint issues before model review",
)
@click.option(
    "--beliefs",
    type=click.Path(exists=True),
    default=None,
    help="Path to beliefs.md file for belief-aware review (from code-expert)",
)
@click.option(
    "--issue",
    type=click.Path(exists=True),
    default=None,
    help="Path to issue description file to check changes against",
)
@click.option(
    "--github-issue",
    default=None,
    help="GitHub issue to check changes against (URL, owner/repo#N, or number)",
)
@click.option(
    "--comment",
    is_flag=True,
    default=False,
    help="Post review as a comment on the PR (requires --pr)",
)
def gate(branch, base, pr, repo, spec, model, output_dir, lint, fix_lint, beliefs, issue, github_issue, comment):
    """
    Run review and exit with code based on result.

    Exit codes:
    - 0: PASS (all models pass)
    - 1: CONCERN (at least one concern, no blocks)
    - 2: BLOCK (at least one model blocks)
    """
    import os

    if comment and not pr:
        click.echo("Error: --comment requires --pr", err=True)
        sys.exit(1)

    models = list(model) if model else DEFAULT_MODELS

    # Generate default output_dir if not specified
    if output_dir is None:
        if pr:
            branch_id = pr_output_dir_name(pr)
        else:
            branch_id = (branch or "staged").replace("/", "-")
        output_dir = os.path.join("reviews", branch_id)
        click.echo(f"Saving review to {output_dir}/", err=True)

    # Lint fix/check (pre-model gate) — skip for PR reviews
    if (fix_lint or lint) and not pr:
        py_files = get_changed_python_files(branch, base, cwd=repo)
        if py_files:
            if fix_lint:
                click.echo(f"Fixing lint issues on {len(py_files)} files...", err=True)
                fix_result = run_lint_fixes(py_files, cwd=repo)
                if fix_result.total_fixed > 0:
                    click.echo(fix_result.summary, err=True)

            click.echo(f"Running lint checks on {len(py_files)} files...", err=True)
            lint_result = run_lint_checks(py_files, cwd=repo)
            if not lint_result.passed:
                click.echo("Lint checks failed:", err=True)
                click.echo(lint_result.summary, err=True)
                sys.exit(2)  # Block on lint failure
            click.echo("Lint checks passed", err=True)

    # Preflight check
    missing = preflight_check(models)
    if missing:
        click.echo(f"Error: Missing CLI tools: {', '.join(missing)}", err=True)
        sys.exit(2)  # Block on missing tools

    # Get diff
    try:
        if pr and repo != ".":
            click.echo(f"Fetching PR branch into {repo}...", err=True)
            head_branch, base_branch, diff_ref = fetch_pr_locally(pr, repo)
            diff_content = get_diff(head_branch, base_branch, cwd=repo)
        elif pr:
            diff_content, diff_ref, _ = get_pr_diff(pr)
        elif branch:
            diff_ref = branch
            diff_content = get_diff(branch, base, cwd=repo)
        else:
            diff_ref = "staged"
            diff_content = get_diff(cwd=repo)
    except (RuntimeError, ValueError) as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(2)

    if not diff_content.strip():
        click.echo("No changes to review.")
        sys.exit(0)

    # Read spec if provided
    spec_content = None
    if spec:
        spec_content = read_file_content(spec)

    # Load beliefs if provided
    beliefs_content = None
    if beliefs:
        beliefs_content = read_file_content(beliefs)
        if beliefs_content:
            click.echo(f"Loaded beliefs from {beliefs}", err=True)
        else:
            click.echo(f"Warning: Could not read beliefs file: {beliefs}", err=True)

    # Load issue if provided
    issue_content = None
    if github_issue:
        try:
            issue_content = get_github_issue(github_issue)
            click.echo(f"Fetched GitHub issue {github_issue}", err=True)
        except (RuntimeError, ValueError) as e:
            click.echo(f"Warning: Could not fetch GitHub issue: {e}", err=True)
    elif issue:
        issue_content = read_file_content(issue)
        if issue_content:
            click.echo(f"Loaded issue from {issue}", err=True)
        else:
            click.echo(f"Warning: Could not read issue file: {issue}", err=True)

    # Build prompt
    prompt = build_review_prompt(diff_content, spec_content, beliefs_content=beliefs_content, issue_content=issue_content)

    # Run reviews
    click.echo(f"Running gate check with {', '.join(models)}...", err=True)
    reviews = asyncio.run(review_with_models(models, prompt))

    # Aggregate
    result = aggregate_reviews(diff_ref, reviews, spec)

    # Save outputs
    os.makedirs(output_dir, exist_ok=True)

    report_path = os.path.join(output_dir, "report.md")
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(format_aggregate_review(result))
    click.echo(f"Saved report to {report_path}", err=True)

    for model_review in reviews:
        raw_path = os.path.join(output_dir, f"{model_review.model}-raw.txt")
        with open(raw_path, "w", encoding="utf-8") as f:
            f.write(model_review.raw_response)
        click.echo(f"Saved {model_review.model} raw response to {raw_path}", err=True)

    # Output summary
    report = format_aggregate_review(result)
    click.echo(format_summary(result))

    # Post as PR comment if requested
    if comment:
        try:
            post_pr_comment(pr, report)
            click.echo(f"Posted review as comment on {diff_ref}", err=True)
        except RuntimeError as e:
            click.echo(f"Error posting comment: {e}", err=True)

    # Exit with appropriate code
    if result.gate == Verdict.PASS:
        sys.exit(0)
    elif result.gate == Verdict.CONCERN:
        sys.exit(1)
    else:
        sys.exit(2)


@cli.command()
@click.option(
    "--branch",
    "-b",
    default=None,
    help="Branch to review (default: staged changes)",
)
@click.option(
    "--base",
    default="main",
    help="Base branch to diff against (default: main)",
)
@click.option(
    "--pr",
    default=None,
    help="GitHub PR to review (URL, owner/repo#N, or number)",
)
@click.option(
    "--repo",
    "-r",
    type=click.Path(exists=True, file_okay=False, dir_okay=True),
    default=".",
    help="Repository directory to analyze (default: current directory)",
)
@click.option(
    "--model",
    "-m",
    multiple=True,
    help="Models to use (default: claude, gemini)",
)
def compare(branch, base, pr, repo, model):
    """Show only disagreements between models."""
    models = list(model) if model else DEFAULT_MODELS

    if len(models) < 2:
        click.echo("Need at least 2 models to compare.", err=True)
        sys.exit(1)

    # Preflight check
    missing = preflight_check(models)
    if missing:
        click.echo(f"Error: Missing CLI tools: {', '.join(missing)}", err=True)
        sys.exit(1)

    # Get diff
    try:
        if pr and repo != ".":
            click.echo(f"Fetching PR branch into {repo}...", err=True)
            head_branch, base_branch, diff_ref = fetch_pr_locally(pr, repo)
            diff_content = get_diff(head_branch, base_branch, cwd=repo)
        elif pr:
            diff_content, diff_ref, _ = get_pr_diff(pr)
        elif branch:
            diff_ref = branch
            diff_content = get_diff(branch, base, cwd=repo)
        else:
            diff_ref = "staged"
            diff_content = get_diff(cwd=repo)
    except (RuntimeError, ValueError) as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)

    if not diff_content.strip():
        click.echo("No changes to compare.")
        sys.exit(0)

    # Build prompt (no spec for simple compare)
    prompt = build_review_prompt(diff_content)

    # Run reviews
    click.echo(f"Running comparison with {', '.join(models)}...", err=True)
    reviews = asyncio.run(review_with_models(models, prompt))

    # Aggregate
    result = aggregate_reviews(diff_ref, reviews)

    # Show only disagreements
    if not result.disagreements:
        click.echo("No disagreements - models agree on all changes.")
    else:
        click.echo(f"Found {len(result.disagreements)} disagreement(s):\n")
        for d in result.disagreements:
            severity = d["severity"]
            change_id = d["change_id"]
            verdicts = d["verdicts"]

            click.echo(f"[{severity}] {change_id}")
            for m, v in verdicts.items():
                click.echo(f"  {m}: {v}")
            click.echo()


@cli.command("check-spec")
@click.argument("spec_file", type=click.Path(exists=True))
@click.option(
    "--branch",
    "-b",
    default=None,
    help="Branch to check (default: staged changes)",
)
@click.option(
    "--base",
    default="main",
    help="Base branch to diff against (default: main)",
)
@click.option(
    "--repo",
    "-r",
    type=click.Path(exists=True, file_okay=False, dir_okay=True),
    default=".",
    help="Repository directory to analyze (default: current directory)",
)
@click.option(
    "--model",
    "-m",
    default="claude",
    help="Model to use (default: claude)",
)
def check_spec(spec_file, branch, base, repo, model):
    """Check code changes against a specification file."""
    # Preflight check
    missing = preflight_check([model])
    if missing:
        click.echo(f"Error: Missing CLI tool: {model}", err=True)
        sys.exit(1)

    # Get diff
    try:
        if branch:
            diff_content = get_diff(branch, base, cwd=repo)
        else:
            diff_content = get_diff(cwd=repo)
    except RuntimeError as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)

    if not diff_content.strip():
        click.echo("No changes to check.")
        sys.exit(0)

    # Read spec
    spec_content = read_file_content(spec_file)
    if spec_content is None:
        click.echo(f"Error: Cannot read spec file: {spec_file}", err=True)
        sys.exit(1)

    # Build spec check prompt
    prompt = build_spec_check_prompt(diff_content, spec_content)

    # Run check
    click.echo(f"Checking spec compliance with {model}...", err=True)
    result = asyncio.run(review_with_model(model, prompt))

    # Output raw response for spec check (different format than review)
    click.echo(result.raw_response)


@cli.command()
@click.option(
    "--branch",
    "-b",
    default=None,
    help="Branch to check (default: staged changes)",
)
@click.option(
    "--base",
    default="main",
    help="Base branch to diff against (default: main)",
)
@click.option(
    "--repo",
    "-r",
    type=click.Path(exists=True, file_okay=False, dir_okay=True),
    default=".",
    help="Repository directory to analyze (default: current directory)",
)
@click.option(
    "--fix",
    is_flag=True,
    default=False,
    help="Auto-fix lint issues (black, isort, ruff --fix)",
)
def lint(branch, base, repo, fix):
    """Run lint checks (black, isort, ruff) on changed files."""
    py_files = get_changed_python_files(branch, base, cwd=repo)

    if not py_files:
        click.echo("No Python files changed.")
        sys.exit(0)

    click.echo(f"{'Fixing' if fix else 'Checking'} {len(py_files)} files:")
    for f in py_files:
        click.echo(f"  {f}")
    click.echo()

    if fix:
        fix_result = run_lint_fixes(py_files, cwd=repo)
        if fix_result.total_fixed > 0:
            click.echo("Fixes applied:")
            click.echo(fix_result.summary)
            click.echo()

        # Re-check after fixing
        lint_result = run_lint_checks(py_files, cwd=repo)
        if lint_result.passed:
            click.echo("All lint checks now pass!")
            sys.exit(0)
        else:
            click.echo("Some issues remain (not auto-fixable):\n")
            click.echo(lint_result.summary)
            sys.exit(1)
    else:
        lint_result = run_lint_checks(py_files, cwd=repo)

        if lint_result.passed:
            click.echo("All lint checks passed!")
            sys.exit(0)
        else:
            click.echo("Lint checks failed:\n")
            click.echo(lint_result.summary)
            click.echo("\nRun with --fix to auto-fix issues.")
            sys.exit(1)


@cli.command()
def models():
    """List available models and their status."""
    from .reviewer import MODEL_COMMANDS, check_model_available

    click.echo("Available models:\n")
    for name, cmd in MODEL_COMMANDS.items():
        available = check_model_available(name)
        status = "available" if available else "not found"
        click.echo(f"  {name}: {cmd[0]} [{status}]")


@cli.command(name="review-loop")
@click.option(
    "--branch",
    "-b",
    default=None,
    help="Branch to review (default: staged changes)",
)
@click.option(
    "--base",
    default="main",
    help="Base branch to diff against (default: main)",
)
@click.option(
    "--pr",
    default=None,
    help="GitHub PR to review (URL, owner/repo#N, or number)",
)
@click.option(
    "--repo",
    "-r",
    type=click.Path(exists=True, file_okay=False, dir_okay=True),
    default=".",
    help="Repository directory to analyze (default: current directory)",
)
@click.option(
    "--spec",
    "-s",
    default=None,
    help="Path to spec file for compliance checking",
)
@click.option(
    "--model",
    "-m",
    multiple=True,
    help="Models to use (default: claude, gemini)",
)
@click.option(
    "--output",
    "-o",
    type=click.Choice(["full", "summary"]),
    default="full",
    help="Output format (default: full)",
)
@click.option(
    "--output-dir",
    "-d",
    type=click.Path(),
    default=None,
    help="Directory to save outputs (report.md + per-model raw responses)",
)
@click.option(
    "--max-iterations",
    "-i",
    default=3,
    help="Maximum observe/review iterations (default: 3)",
)
@click.option(
    "--beliefs",
    type=click.Path(exists=True),
    default=None,
    help="Path to beliefs.md file for belief-aware review (from code-expert)",
)
@click.option(
    "--issue",
    type=click.Path(exists=True),
    default=None,
    help="Path to issue description file to check changes against",
)
@click.option(
    "--github-issue",
    default=None,
    help="GitHub issue to check changes against (URL, owner/repo#N, or number)",
)
@click.option(
    "--comment",
    is_flag=True,
    default=False,
    help="Post review as a comment on the PR (requires --pr)",
)
@click.option(
    "--run-tests/--no-run-tests",
    default=False,
    help="Run pytest on related test files and include results in review context",
)
@click.option(
    "--coverage-map/--no-coverage-map",
    "use_coverage_map",
    default=False,
    help="Auto-run 'coverage-map collect' if coverage-map.json is missing or stale",
)
def review_loop(branch, base, pr, repo, spec, model, output, output_dir, max_iterations, beliefs, issue, github_issue, comment, run_tests, use_coverage_map):
    """
    Run automated observe/review loop.

    Automatically gathers observations, runs them, and reviews with context.
    If the review requests additional observations, repeats up to --max-iterations times.

    This is the recommended way to run code review for best results.
    """
    import os
    from datetime import datetime

    from .reviewer import parse_observations

    if comment and not pr:
        click.echo("Error: --comment requires --pr", err=True)
        sys.exit(1)

    models = list(model) if model else DEFAULT_MODELS

    # Generate default output_dir if not specified
    if output_dir is None:
        timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        if pr:
            branch_id = pr_output_dir_name(pr)
        else:
            branch_id = (branch or "staged").replace("/", "-")
        output_dir = os.path.join("reviews", branch_id, timestamp)
        click.echo(f"Saving review to {output_dir}/", err=True)

    # Preflight check
    missing = preflight_check(models)
    if missing:
        click.echo(f"Error: Missing CLI tools: {', '.join(missing)}", err=True)
        sys.exit(1)

    # Get diff
    try:
        if pr and repo != ".":
            click.echo(f"Fetching PR branch into {repo}...", err=True)
            head_branch, base_branch, diff_ref = fetch_pr_locally(pr, repo)
            diff_content = get_diff(head_branch, base_branch, cwd=repo)
        elif pr:
            diff_content, diff_ref, _ = get_pr_diff(pr)
        elif branch:
            diff_ref = branch
            diff_content = get_diff(branch, base, cwd=repo)
        else:
            diff_ref = "staged"
            diff_content = get_diff(cwd=repo)
    except (RuntimeError, ValueError) as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)

    if not diff_content.strip():
        click.echo("No changes to review.", err=True)
        sys.exit(0)

    # Read spec if provided
    spec_content = None
    if spec:
        spec_content = read_file_content(spec)
        if spec_content is None:
            click.echo(f"Warning: Spec file not found: {spec}", err=True)

    # Load beliefs if provided
    beliefs_content = None
    if beliefs:
        beliefs_content = read_file_content(beliefs)
        if beliefs_content:
            click.echo(f"Loaded beliefs from {beliefs}", err=True)
        else:
            click.echo(f"Warning: Could not read beliefs file: {beliefs}", err=True)

    # Load issue if provided
    issue_content = None
    if github_issue:
        try:
            issue_content = get_github_issue(github_issue)
            click.echo(f"Fetched GitHub issue {github_issue}", err=True)
        except (RuntimeError, ValueError) as e:
            click.echo(f"Warning: Could not fetch GitHub issue: {e}", err=True)
    elif issue:
        issue_content = read_file_content(issue)
        if issue_content:
            click.echo(f"Loaded issue from {issue}", err=True)
        else:
            click.echo(f"Warning: Could not read issue file: {issue}", err=True)

    # Use first model for observation gathering
    observe_model = models[0]
    all_observations = {}

    # Create output_dir early so we can save iteration artifacts
    os.makedirs(output_dir, exist_ok=True)

    # Auto-run coverage-map collect if requested and stale/missing
    changed_files = extract_changed_files(diff_content)
    python_files = [f for f in changed_files if f.endswith(".py") and not f.startswith("tests/")]

    if use_coverage_map and python_files:
        import subprocess as _sp

        cov_json = os.path.join(repo, "coverage-map.json")
        needs_collect = not os.path.exists(cov_json)

        if not needs_collect:
            # Check staleness: if any source file is newer than coverage-map.json
            cov_mtime = os.path.getmtime(cov_json)
            for src in python_files:
                src_path = os.path.join(repo, src)
                if os.path.exists(src_path) and os.path.getmtime(src_path) > cov_mtime:
                    needs_collect = True
                    break

        if needs_collect:
            click.echo("Running coverage-map collect (this may take a while)...", err=True)
            try:
                proc = _sp.run(
                    ["coverage-map", "collect"],
                    capture_output=True, text=True,
                    cwd=repo, timeout=300,
                )
                if proc.returncode == 0:
                    click.echo("coverage-map collect completed successfully", err=True)
                else:
                    click.echo(
                        click.style(f"WARNING: coverage-map collect failed: {proc.stderr[:200]}", fg="yellow"),
                        err=True,
                    )
            except FileNotFoundError:
                click.echo(
                    click.style("WARNING: coverage-map not installed, skipping collect", fg="yellow"),
                    err=True,
                )
            except _sp.TimeoutExpired:
                click.echo(
                    click.style("WARNING: coverage-map collect timed out after 300s", fg="yellow"),
                    err=True,
                )
        else:
            click.echo("coverage-map.json is up to date", err=True)

    # Auto-lookup coverage-map for changed files (parallel)
    if python_files:
        click.echo(f"Auto-lookup: {len(python_files)} Python file(s) changed", err=True)
        coverage_obs = asyncio.run(_gather_coverage_lookups(python_files, repo))
        for obs_name, result in coverage_obs.items():
            all_observations[obs_name] = result
            click.echo(f"  {result.get('source_file', obs_name)}: {result.get('test_count', 0)} tests", err=True)
        if all_observations:
            # Save auto-observations
            with open(os.path.join(output_dir, "00-auto-coverage.json"), "w") as f:
                json.dump(all_observations, f, indent=2, default=str)
            click.echo(f"Auto-lookup found tests for {len(all_observations)} file(s)", err=True)

    # Auto-gather full function bodies for modified functions
    if repo != ".":
        click.echo("Auto-gathering modified function bodies...", err=True)
        fn_context = asyncio.run(gather_function_context(diff_content, repo))
        if fn_context:
            all_observations.update(fn_context)
            click.echo(f"Auto-gathered {len(fn_context)} function body(ies):", err=True)
            for key in fn_context:
                click.echo(f"  {key}", err=True)
            with open(os.path.join(output_dir, "00-auto-functions.json"), "w") as f:
                json.dump(fn_context, f, indent=2, default=str)

    # Auto-discover related test files for modified code
    if repo != ".":
        click.echo("Auto-discovering related test files...", err=True)
        test_context = asyncio.run(gather_related_test_files(diff_content, repo))
        test_files = test_context.get("test_files", [])
        if test_files:
            # Add each test file's content as an observation
            for tf in test_files:
                obs_key = f"related_test:{tf['path']}"
                all_observations[obs_key] = {
                    "path": tf["path"],
                    "covers": tf["covers"],
                    "symbols_referenced": tf["symbols_referenced"],
                    "content": tf["content"],
                    "truncated": tf.get("truncated", False),
                    "line_count": tf["line_count"],
                }
            click.echo(f"Auto-discovered {len(test_files)} related test file(s):", err=True)
            for tf in test_files:
                symbols = ", ".join(tf["symbols_referenced"][:5]) if tf["symbols_referenced"] else "no direct symbol refs"
                click.echo(f"  {tf['path']} ({symbols})", err=True)

            # Flag duplicate coverage
            dupes = test_context.get("duplicate_coverage", {})
            if dupes:
                click.echo(f"Duplicate test coverage detected:", err=True)
                for src, tests in dupes.items():
                    click.echo(f"  {src} covered by: {', '.join(tests)}", err=True)

            with open(os.path.join(output_dir, "00-auto-tests.json"), "w") as f:
                json.dump(test_context, f, indent=2, default=str)
        else:
            click.echo("No related test files found.", err=True)

    # Auto-extract file references from beliefs content
    if beliefs_content and repo != ".":
        import re as _re
        belief_file_refs = _re.findall(r'[\w/]+\.py', beliefs_content)
        belief_file_refs = sorted(set(belief_file_refs))
        if belief_file_refs:
            click.echo(f"Auto-extracting imports from {len(belief_file_refs)} belief-referenced file(s)...", err=True)
            for ref_path in belief_file_refs:
                full_ref = Path(repo) / ref_path
                if full_ref.exists():
                    obs_key = f"belief_ref:{ref_path}"
                    ref_result = asyncio.run(file_imports(ref_path, repo_path=repo))
                    all_observations[obs_key] = ref_result
                    click.echo(f"  {ref_path}: {len(ref_result.get('imports', []))} imports", err=True)

    # Run tests if requested
    if run_tests and python_files:
        click.echo("Running pytest on related test files...", err=True)
        test_results = asyncio.run(run_tests_for_files(python_files, repo))
        status = test_results["status"]
        if status != "SKIPPED":
            passed = test_results["passed"]
            failed = test_results["failed"]
            errors = test_results["errors"]
            duration = test_results["duration_seconds"]
            click.echo(
                f"Tests: {passed} passed, {failed} failed, {errors} errors ({duration}s) — {status}",
                err=True,
            )
            # Add formatted results to observations
            all_observations["test_execution"] = test_results
            all_observations["test_execution_summary"] = format_test_results(test_results)
            with open(os.path.join(output_dir, "00-auto-test-results.json"), "w") as f:
                json.dump(test_results, f, indent=2, default=str)
        else:
            click.echo(f"Tests skipped: {test_results['output']}", err=True)

    # Check test discoverability for new test files
    if changed_files:
        disc_result = check_test_discoverability(changed_files, repo)
        if not disc_result.passed:
            click.echo(click.style("Test discoverability warnings:", fg="yellow"), err=True)
            for warning in disc_result.warnings:
                click.echo(click.style(f"  {warning}", fg="yellow"), err=True)
            all_observations["test_discoverability"] = {
                "warnings": disc_result.warnings,
                "passed": False,
            }
    for iteration in range(1, max_iterations + 1):
        click.echo(f"\n=== Iteration {iteration}/{max_iterations} ===", err=True)
        iter_prefix = f"{iteration:02d}"

        # Observe pass
        click.echo(f"Gathering observations with {observe_model}...", err=True)
        observe_prompt = build_observe_prompt(diff_content)

        # Save observe prompt
        with open(os.path.join(output_dir, f"{iter_prefix}-observe-prompt.txt"), "w") as f:
            f.write(observe_prompt)

        # Run observe model and save response
        observe_response = asyncio.run(run_model(observe_model, observe_prompt))
        with open(os.path.join(output_dir, f"{iter_prefix}-observe-response.txt"), "w") as f:
            f.write(observe_response)

        requested_obs = parse_observe_response(observe_response)

        if requested_obs:
            click.echo(f"Requested {len(requested_obs)} observation(s):", err=True)
            for obs in requested_obs:
                click.echo(f"  - {obs.get('tool')}: {obs.get('name')}", err=True)

            # Run observations and save results
            click.echo("Running observations...", err=True)
            new_obs = asyncio.run(run_observations(requested_obs, repo))
            all_observations.update(new_obs)

            # Flag any failed observations
            failed_obs = [(name, result) for name, result in new_obs.items() if isinstance(result, dict) and "error" in result]
            if failed_obs:
                click.echo(click.style(f"WARNING: {len(failed_obs)} observation(s) failed:", fg="yellow"), err=True)
                for name, result in failed_obs:
                    click.echo(click.style(f"  - {name}: {result.get('error', 'Unknown error')}", fg="yellow"), err=True)

            with open(os.path.join(output_dir, f"{iter_prefix}-observations.json"), "w") as f:
                json.dump(new_obs, f, indent=2, default=str)

            succeeded = len(new_obs) - len(failed_obs)
            click.echo(f"Total observations: {len(all_observations)} ({succeeded} succeeded, {len(failed_obs)} failed)", err=True)
        else:
            click.echo("No new observations requested.", err=True)

        # Review pass
        click.echo(f"Running review with {', '.join(models)}...", err=True)
        review_prompt = build_review_prompt(diff_content, spec_content, observations=all_observations if all_observations else None, beliefs_content=beliefs_content, issue_content=issue_content)

        # Save review prompt
        with open(os.path.join(output_dir, f"{iter_prefix}-review-prompt.txt"), "w") as f:
            f.write(review_prompt)

        reviews = asyncio.run(review_with_models(models, review_prompt, observations=all_observations if all_observations else None))

        # Save per-model responses
        for model_review in reviews:
            with open(os.path.join(output_dir, f"{iter_prefix}-{model_review.model}-response.txt"), "w") as f:
                f.write(model_review.raw_response)

        # Check if any model requested more observations
        more_obs_requested = []
        for review_result in reviews:
            obs_requests = parse_observations(review_result.raw_response)
            if obs_requests:
                more_obs_requested.extend(obs_requests)

        if not more_obs_requested:
            click.echo("No additional observations requested. Review complete.", err=True)
            break

        if iteration < max_iterations:
            click.echo(f"Models requested {len(more_obs_requested)} more observation(s), continuing...", err=True)
        else:
            click.echo(f"Max iterations reached. Finalizing review.", err=True)

    # Aggregate and output
    result = aggregate_reviews(diff_ref, reviews, spec)

    if output == "full":
        report = format_aggregate_review(result)
    else:
        report = format_summary(result)

    # Save final report (output_dir already created in loop)
    report_path = os.path.join(output_dir, "report.md")
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(report)
    click.echo(f"Saved report to {report_path}", err=True)

    # Save aggregated observations
    if all_observations:
        obs_path = os.path.join(output_dir, "observations.json")
        with open(obs_path, "w", encoding="utf-8") as f:
            json.dump(all_observations, f, indent=2, default=str)
        click.echo(f"Saved observations to {obs_path}", err=True)

    click.echo(report)

    # Post as PR comment if requested
    if comment:
        try:
            post_pr_comment(pr, report)
            click.echo(f"Posted review as comment on {diff_ref}", err=True)
        except RuntimeError as e:
            click.echo(f"Error posting comment: {e}", err=True)


@cli.command()
@click.argument("paths", nargs=-1, required=True)
@click.option(
    "--repo",
    "-r",
    type=click.Path(exists=True, file_okay=False, dir_okay=True),
    default=".",
    help="Repository directory (default: current directory)",
)
@click.option(
    "--spec",
    "-s",
    default=None,
    help="Path to spec file for compliance checking",
)
@click.option(
    "--model",
    "-m",
    multiple=True,
    help="Models to use (default: claude, gemini)",
)
@click.option(
    "--output-dir",
    "-d",
    type=click.Path(),
    default=None,
    help="Directory to save outputs",
)
@click.option(
    "--glob",
    "-g",
    is_flag=True,
    help="Treat paths as glob patterns",
)
@click.option(
    "--fix-blocks",
    is_flag=True,
    default=False,
    help="Automatically attempt to fix BLOCK verdicts",
)
@click.option(
    "--beliefs",
    type=click.Path(exists=True),
    default=None,
    help="Path to beliefs.md file for belief-aware review (from code-expert)",
)
@click.option(
    "--issue",
    type=click.Path(exists=True),
    default=None,
    help="Path to issue description file to check changes against",
)
def files(paths, repo, spec, model, output_dir, glob, fix_blocks, beliefs, issue):
    """
    Review specific files or directories (not diffs).

    Examples:
        code-review files src/auth/client.py
        code-review files src/auth/*.py --glob
        code-review files src/
    """
    import os
    from datetime import datetime
    from pathlib import Path

    from .reviewer import parse_observations

    models = list(model) if model else DEFAULT_MODELS

    # Collect files to review
    files_to_review = []
    repo_path = Path(repo)

    for path_arg in paths:
        if glob:
            # Treat as glob pattern
            import fnmatch
            for root, dirs, filenames in os.walk(repo_path):
                for filename in filenames:
                    full_path = Path(root) / filename
                    rel_path = full_path.relative_to(repo_path)
                    if fnmatch.fnmatch(str(rel_path), path_arg):
                        files_to_review.append(full_path)
        else:
            full_path = repo_path / path_arg if not Path(path_arg).is_absolute() else Path(path_arg)
            if full_path.is_dir():
                # Add all Python files in directory
                for py_file in full_path.rglob("*.py"):
                    files_to_review.append(py_file)
            elif full_path.exists():
                files_to_review.append(full_path)
            else:
                click.echo(f"Warning: Path not found: {path_arg}", err=True)

    if not files_to_review:
        click.echo("No files found to review.", err=True)
        sys.exit(1)

    # Filter to Python files only
    files_to_review = [f for f in files_to_review if f.suffix == ".py"]
    files_to_review = sorted(set(files_to_review))

    click.echo(f"Reviewing {len(files_to_review)} file(s):", err=True)
    for f in files_to_review[:10]:
        rel = f.relative_to(repo_path) if f.is_relative_to(repo_path) else f
        click.echo(f"  {rel}", err=True)
    if len(files_to_review) > 10:
        click.echo(f"  ... and {len(files_to_review) - 10} more", err=True)

    # Generate output_dir if not specified
    if output_dir is None:
        timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        output_dir = os.path.join("reviews", "files", timestamp)
        click.echo(f"Saving review to {output_dir}/", err=True)

    os.makedirs(output_dir, exist_ok=True)

    # Preflight check
    missing = preflight_check(models)
    if missing:
        click.echo(f"Error: Missing CLI tools: {', '.join(missing)}", err=True)
        sys.exit(1)

    # Build pseudo-diff from file contents
    diff_parts = []
    for file_path in files_to_review:
        try:
            content = file_path.read_text()
            rel_path = file_path.relative_to(repo_path) if file_path.is_relative_to(repo_path) else file_path
            # Format as a new file in diff format
            lines = content.split("\n")
            diff_part = f"diff --git a/{rel_path} b/{rel_path}\n"
            diff_part += f"--- /dev/null\n"
            diff_part += f"+++ b/{rel_path}\n"
            diff_part += f"@@ -0,0 +1,{len(lines)} @@\n"
            diff_part += "\n".join(f"+{line}" for line in lines)
            diff_parts.append(diff_part)
        except Exception as e:
            click.echo(f"Warning: Could not read {file_path}: {e}", err=True)

    diff_content = "\n".join(diff_parts)
    diff_ref = f"files:{len(files_to_review)}"

    # Read spec if provided
    spec_content = None
    if spec:
        spec_content = read_file_content(spec)
        if spec_content is None:
            click.echo(f"Warning: Spec file not found: {spec}", err=True)

    # Load beliefs if provided
    beliefs_content = None
    if beliefs:
        beliefs_content = read_file_content(beliefs)
        if beliefs_content:
            click.echo(f"Loaded beliefs from {beliefs}", err=True)
        else:
            click.echo(f"Warning: Could not read beliefs file: {beliefs}", err=True)

    # Load issue if provided
    issue_content = None
    if issue:
        issue_content = read_file_content(issue)
        if issue_content:
            click.echo(f"Loaded issue from {issue}", err=True)
        else:
            click.echo(f"Warning: Could not read issue file: {issue}", err=True)

    # Use first model for observation gathering
    observe_model = models[0]
    all_observations = {}

    # Auto-lookup coverage-map for files (parallel)
    python_files = [str(f.relative_to(repo_path)) for f in files_to_review if not str(f).startswith("tests")]
    if python_files:
        click.echo(f"Auto-lookup: checking coverage for {len(python_files)} file(s)", err=True)
        all_observations = asyncio.run(_gather_coverage_lookups(python_files, str(repo_path)))
        for obs_name, result in all_observations.items():
            click.echo(f"  {result.get('source_file', obs_name)}: {result.get('test_count', 0)} tests", err=True)
        if all_observations:
            with open(os.path.join(output_dir, "00-auto-coverage.json"), "w") as f:
                json.dump(all_observations, f, indent=2, default=str)

    # Auto-discover related test files
    if diff_content and str(repo_path) != ".":
        click.echo("Auto-discovering related test files...", err=True)
        test_context = asyncio.run(gather_related_test_files(diff_content, str(repo_path)))
        test_files = test_context.get("test_files", [])
        if test_files:
            for tf in test_files:
                obs_key = f"related_test:{tf['path']}"
                all_observations[obs_key] = {
                    "path": tf["path"],
                    "covers": tf["covers"],
                    "symbols_referenced": tf["symbols_referenced"],
                    "content": tf["content"],
                    "truncated": tf.get("truncated", False),
                    "line_count": tf["line_count"],
                }
            click.echo(f"Auto-discovered {len(test_files)} related test file(s)", err=True)
            with open(os.path.join(output_dir, "00-auto-tests.json"), "w") as f:
                json.dump(test_context, f, indent=2, default=str)

    # Single iteration for files review (no observe loop needed - we have full context)
    click.echo(f"\nRunning review with {', '.join(models)}...", err=True)
    review_prompt = build_review_prompt(diff_content, spec_content, observations=all_observations if all_observations else None, beliefs_content=beliefs_content, issue_content=issue_content)

    # Save review prompt
    with open(os.path.join(output_dir, "review-prompt.txt"), "w") as f:
        f.write(review_prompt)

    reviews = asyncio.run(review_with_models(models, review_prompt, observations=all_observations if all_observations else None))

    # Save per-model responses
    for model_review in reviews:
        with open(os.path.join(output_dir, f"{model_review.model}-response.txt"), "w") as f:
            f.write(model_review.raw_response)

    # Aggregate and output
    result = aggregate_reviews(diff_ref, reviews, spec)

    report = format_aggregate_review(result)

    # Save final report
    report_path = os.path.join(output_dir, "report.md")
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(report)
    click.echo(f"Saved report to {report_path}", err=True)

    if all_observations:
        obs_path = os.path.join(output_dir, "observations.json")
        with open(obs_path, "w", encoding="utf-8") as f:
            json.dump(all_observations, f, indent=2, default=str)

    click.echo(report)

    # Attempt to fix BLOCKs if requested
    if fix_blocks and result.gate == Verdict.BLOCK:
        # Extract BLOCKs from reviews
        blocks = []
        for model_review in reviews:
            for change in model_review.changes:
                if change.verdict == Verdict.BLOCK:
                    blocks.append({
                        "file": change.change_id,
                        "concern": change.reasoning,
                    })

        # Deduplicate by file (keep first concern per file)
        seen_files = set()
        unique_blocks = []
        for block in blocks:
            if block["file"] not in seen_files:
                seen_files.add(block["file"])
                unique_blocks.append(block)

        if unique_blocks:
            click.echo(f"\n--- Fix Blocks ---", err=True)
            click.echo(f"Attempting to fix {len(unique_blocks)} BLOCK(s)...", err=True)

            fix_results = asyncio.run(fix_blocks_async(unique_blocks, models[0], repo))

            # Show results
            fixed_count = 0
            for r in fix_results:
                if r["status"] == "fixed":
                    click.echo(f"  ✓ {r['file']}: fixed", err=True)
                    fixed_count += 1
                else:
                    click.echo(f"  ✗ {r['file']}: {r.get('error', 'failed')}", err=True)

            # Save patches
            patches_dir = os.path.join(output_dir, "patches")
            os.makedirs(patches_dir, exist_ok=True)
            for i, r in enumerate(fix_results):
                if r.get("patch"):
                    patch_filename = f"{i:02d}-{Path(r['file']).name}.patch"
                    patch_path = os.path.join(patches_dir, patch_filename)
                    with open(patch_path, "w") as f:
                        f.write(r["patch"])
                    click.echo(f"  Saved patch: {patch_path}", err=True)

            if fixed_count > 0:
                click.echo(f"\nApplied {fixed_count} fix(es). Run review again to verify.", err=True)
            else:
                click.echo(f"\nNo fixes could be applied.", err=True)


@cli.command("install-skill")
@click.option(
    "--skill-dir",
    type=click.Path(),
    default=None,
    help="Target directory for skill file (default: .claude/skills/code-review)",
)
def install_skill(skill_dir):
    """Install the code-review skill file for Claude Code."""
    from pathlib import Path

    from .skill import SKILL_CONTENT

    # Determine target directory
    if skill_dir:
        target_dir = Path(skill_dir)
    else:
        target_dir = Path.cwd() / ".claude" / "skills" / "code-review"

    target_dir.mkdir(parents=True, exist_ok=True)
    target_file = target_dir / "SKILL.md"

    target_file.write_text(SKILL_CONTENT)
    click.echo(f"Installed skill to {target_file}")


if __name__ == "__main__":
    cli()
