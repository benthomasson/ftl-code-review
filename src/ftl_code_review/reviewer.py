"""Model invocation and response parsing for code review."""

import asyncio
import json
import os
import re
import shutil

from . import (
    ChangeVerdict,
    Correctness,
    Integration,
    ModelReview,
    SelfReview,
    SpecCompliance,
    TestCoverage,
    Verdict,
)
from .observations import run_observations

# Model CLI commands - extend this dict to add new models
# Note: gemini requires empty string after -p to read prompt from stdin
MODEL_COMMANDS: dict[str, list[str]] = {
    "claude": ["claude", "-p"],
    "gemini": ["gemini", "-p", ""],  # empty arg is workaround for stdin input
}

# Default timeout for model invocation (5 minutes)
DEFAULT_TIMEOUT = 300


def check_model_available(model: str) -> bool:
    """Check if a model's CLI is available."""
    if model not in MODEL_COMMANDS:
        return False
    cmd = MODEL_COMMANDS[model][0]
    return shutil.which(cmd) is not None


def preflight_check(models: list[str]) -> list[str]:
    """Check which models are available, return list of missing ones."""
    missing = []
    for model in models:
        if not check_model_available(model):
            missing.append(model)
    return missing


async def run_model(model: str, prompt: str, timeout: int = DEFAULT_TIMEOUT) -> str:
    """
    Invoke model via CLI, piping prompt through stdin.

    Args:
        model: Model name (must be in MODEL_COMMANDS)
        prompt: Full prompt text to send
        timeout: Timeout in seconds

    Returns:
        Model's response text

    Raises:
        ValueError: If model not supported
        TimeoutError: If model doesn't respond in time
        RuntimeError: If model invocation fails
    """
    if model not in MODEL_COMMANDS:
        raise ValueError(f"Unknown model: {model}. Available: {list(MODEL_COMMANDS.keys())}")

    cmd = MODEL_COMMANDS[model]

    # Remove CLAUDECODE env var to allow nested claude invocation
    env = {k: v for k, v in os.environ.items() if k != "CLAUDECODE"}

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
    )

    try:
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(prompt.encode()),
            timeout=timeout,
        )
    except TimeoutError:
        proc.kill()
        raise TimeoutError(f"Model {model} timed out after {timeout}s") from None

    if proc.returncode != 0:
        raise RuntimeError(f"Model {model} failed: {stderr.decode()}")

    return stdout.decode()


def parse_verdict(text: str) -> Verdict:
    """Parse verdict string to enum."""
    text = text.strip().upper()
    if text == "PASS":
        return Verdict.PASS
    elif text == "CONCERN":
        return Verdict.CONCERN
    elif text == "BLOCK":
        return Verdict.BLOCK
    else:
        # Conservative default
        return Verdict.CONCERN


def parse_correctness(text: str) -> Correctness | None:
    """Parse correctness string to enum."""
    text = text.strip().upper()
    if text == "VALID":
        return Correctness.VALID
    elif text == "QUESTIONABLE":
        return Correctness.QUESTIONABLE
    elif text == "BROKEN":
        return Correctness.BROKEN
    return None


def parse_spec_compliance(text: str) -> SpecCompliance | None:
    """Parse spec compliance string to enum."""
    text = text.strip().upper()
    if text == "MEETS":
        return SpecCompliance.MEETS
    elif text == "PARTIAL":
        return SpecCompliance.PARTIAL
    elif text == "VIOLATES":
        return SpecCompliance.VIOLATES
    elif text in ("N/A", "NA"):
        return SpecCompliance.NA
    return None


def parse_test_coverage(text: str) -> TestCoverage | None:
    """Parse test coverage string to enum."""
    text = text.strip().upper()
    if text == "COVERED":
        return TestCoverage.COVERED
    elif text == "PARTIAL":
        return TestCoverage.PARTIAL
    elif text == "UNTESTED":
        return TestCoverage.UNTESTED
    return None


def parse_integration(text: str) -> Integration | None:
    """Parse integration string to enum."""
    text = text.strip().upper()
    if text == "WIRED":
        return Integration.WIRED
    elif text == "PARTIAL":
        return Integration.PARTIAL
    elif text == "MISSING":
        return Integration.MISSING
    return None


# Pattern to match change verdicts in model output
CHANGE_PATTERN = re.compile(
    r"###\s+(.+?)\s*\n"
    r"VERDICT:\s*(PASS|CONCERN|BLOCK)\s*\n"
    r"(?:CORRECTNESS:\s*(\w+)\s*\n)?"
    r"(?:SPEC_COMPLIANCE:\s*([^\n]+)\s*\n)?"
    r"(?:ISSUE_COMPLIANCE:\s*([^\n]+)\s*\n)?"
    r"(?:BELIEF_COMPLIANCE:\s*([^\n]+)\s*\n)?"
    r"(?:TEST_COVERAGE:\s*(\w+)\s*\n)?"
    r"(?:INTEGRATION:\s*(\w+)\s*\n)?"
    r"REASONING:\s*(.*?)(?=\n---|\n###|\n##|$)",
    re.DOTALL | re.IGNORECASE,
)

# Pattern to match self-review section
SELF_REVIEW_PATTERN = re.compile(
    r"###\s*SELF_REVIEW\s*\n"
    r"(?:CONFIDENCE:\s*(?:HIGH|MEDIUM|LOW)\s*\n)?"
    r"LIMITATIONS:\s*(.*?)(?=\n---|\n###|\n##|$)",
    re.DOTALL | re.IGNORECASE,
)

# Pattern to match feature requests section
FEATURE_REQUESTS_PATTERN = re.compile(
    r"###\s*FEATURE_REQUESTS\s*\n" r"(.*?)(?=\n---|\n###|\n##|$)",
    re.DOTALL | re.IGNORECASE,
)

# Pattern to match observations section
OBSERVATIONS_PATTERN = re.compile(
    r"###\s*OBSERVATIONS\s*\n```json\n(.*?)\n```",
    re.DOTALL | re.IGNORECASE,
)


def parse_self_review(response: str) -> SelfReview | None:
    """Parse self-review section from response."""
    match = SELF_REVIEW_PATTERN.search(response)
    if not match:
        return None

    limitations = match.group(1).strip() if match.group(1) else ""

    return SelfReview(limitations=limitations)


def parse_feature_requests(response: str) -> list[str]:
    """Parse feature requests section from response."""
    match = FEATURE_REQUESTS_PATTERN.search(response)
    if not match:
        return []

    content = match.group(1).strip()
    # Parse bullet points (- item)
    requests = []
    for line in content.split("\n"):
        line = line.strip()
        if line.startswith("- ") or line.startswith("* "):
            requests.append(line[2:].strip())

    return requests


def parse_observations(response: str) -> list[dict]:
    """
    Parse observation requests from response.

    Args:
        response: Raw model response

    Returns:
        List of observation request dicts, or empty list if none
    """
    match = OBSERVATIONS_PATTERN.search(response)
    if not match:
        return []

    try:
        obs_json = match.group(1).strip()
        observations = json.loads(obs_json)
        if isinstance(observations, list):
            return observations
        return []
    except json.JSONDecodeError:
        return []


def parse_review_response(model: str, response: str) -> ModelReview:
    """
    Parse model response into structured review.

    Args:
        model: Model name
        response: Raw model response text

    Returns:
        ModelReview with parsed changes
    """
    changes: list[ChangeVerdict] = []

    for match in CHANGE_PATTERN.finditer(response):
        change_id = match.group(1).strip()
        verdict = parse_verdict(match.group(2))
        correctness = parse_correctness(match.group(3)) if match.group(3) else None
        spec_compliance = parse_spec_compliance(match.group(4)) if match.group(4) else None
        # group 5 is ISSUE_COMPLIANCE (not stored on ChangeVerdict)
        belief_compliance = match.group(6).strip() if match.group(6) else None
        test_coverage = parse_test_coverage(match.group(7)) if match.group(7) else None
        integration = parse_integration(match.group(8)) if match.group(8) else None
        reasoning = match.group(9).strip() if match.group(9) else ""

        # Append belief compliance info to reasoning if present
        if belief_compliance and belief_compliance.upper() not in ("N/A", "CONSISTENT"):
            reasoning = f"[BELIEF: {belief_compliance}] {reasoning}"

        changes.append(
            ChangeVerdict(
                change_id=change_id,
                verdict=verdict,
                correctness=correctness,
                spec_compliance=spec_compliance,
                test_coverage=test_coverage,
                integration=integration,
                reasoning=reasoning,
            )
        )

    # Determine overall gate - BLOCK > CONCERN > PASS
    gate = Verdict.PASS
    for change in changes:
        if change.verdict == Verdict.BLOCK:
            gate = Verdict.BLOCK
            break
        elif change.verdict == Verdict.CONCERN:
            gate = Verdict.CONCERN

    # If no changes parsed, default to CONCERN (conservative)
    if not changes:
        gate = Verdict.CONCERN

    # Parse self-review and feature requests
    self_review = parse_self_review(response)
    feature_requests = parse_feature_requests(response)

    return ModelReview(
        model=model,
        gate=gate,
        changes=changes,
        raw_response=response,
        self_review=self_review,
        feature_requests=feature_requests,
    )


def parse_observe_response(response: str) -> list[dict]:
    """
    Parse observation requests from observe-only prompt response.

    Args:
        response: Raw model response (should be JSON array)

    Returns:
        List of observation request dicts
    """
    # Try to find JSON array in response
    # Look for ```json block first
    json_match = re.search(r"```json\n(.*?)\n```", response, re.DOTALL)
    if json_match:
        try:
            return json.loads(json_match.group(1))
        except json.JSONDecodeError:
            pass

    # Try parsing the whole response as JSON
    try:
        result = json.loads(response.strip())
        if isinstance(result, list):
            return result
    except json.JSONDecodeError:
        pass

    return []


async def observe_with_model(model: str, diff_content: str) -> list[dict]:
    """
    Run observation-gathering pass with a model.

    Args:
        model: Model name
        diff_content: Diff content to analyze

    Returns:
        List of observation requests
    """
    from .prompts.observe import build_observe_prompt

    prompt = build_observe_prompt(diff_content)
    response = await run_model(model, prompt)
    return parse_observe_response(response)


async def review_with_model(
    model: str,
    prompt: str,
    observations: dict | None = None,
) -> ModelReview:
    """
    Run review with a single model.

    Args:
        model: Model name
        prompt: Review prompt
        observations: Optional observation results to include in review

    Returns:
        Parsed ModelReview
    """
    try:
        response = await run_model(model, prompt)
        review = parse_review_response(model, response)

        if observations:
            review.observations = observations

        return review

    except Exception as e:
        # On error, return BLOCK with error message
        return ModelReview(
            model=model,
            gate=Verdict.BLOCK,
            changes=[
                ChangeVerdict(
                    change_id="ERROR",
                    verdict=Verdict.BLOCK,
                    reasoning=f"Model invocation failed: {e}",
                )
            ],
            raw_response=str(e),
        )


async def review_with_models(
    models: list[str],
    prompt: str,
    observations: dict | None = None,
) -> list[ModelReview]:
    """
    Run review with multiple models concurrently.

    Args:
        models: List of model names
        prompt: Review prompt (same for all models)
        observations: Optional observation results to include

    Returns:
        List of ModelReview, one per model
    """
    tasks = [
        review_with_model(model, prompt, observations)
        for model in models
    ]
    return await asyncio.gather(*tasks)
