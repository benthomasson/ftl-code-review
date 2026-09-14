"""Model invocation and response parsing for code review."""

import asyncio
import json
import os
import re
import shutil
import threading
import urllib.error
import urllib.request

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

# Model CLI commands. API-backed providers are handled by run_model directly.
# Note: gemini requires empty string after -p to read prompt from stdin.
MODEL_COMMANDS: dict[str, list[str]] = {
    "claude": ["claude", "-p"],
    "gemini": ["gemini", "-p", ""],  # empty arg is workaround for stdin input
}

API_MODELS = {"openai"}
OPENAI_API_URL = "https://api.openai.com/v1/chat/completions"
OPENAI_MODEL = "gpt-4o-mini"

# Default timeout for model invocation (5 minutes)
DEFAULT_TIMEOUT = 300


def _model_provider(model: str) -> tuple[str, str | None]:
    """Return the provider and optional model name from a model spec.

    API providers may be selected as ``openai:model-name`` while the plain
    ``openai`` form continues to use ``OPENAI_MODEL`` (or its default).
    CLI providers do not currently accept a model suffix.
    """
    provider, separator, model_name = model.partition(":")
    return provider, (model_name or None) if separator else None


def check_model_available(model: str) -> bool:
    """Check whether a model provider is configured and available."""
    provider, _ = _model_provider(model)
    if provider in API_MODELS:
        return bool(os.environ.get("OPENAI_API_KEY"))
    if provider not in MODEL_COMMANDS or model != provider:
        return False
    cmd = MODEL_COMMANDS[provider][0]
    return shutil.which(cmd) is not None


def preflight_check(models: list[str]) -> list[str]:
    """Check which models are available, return list of missing ones."""
    missing = []
    for model in models:
        if not check_model_available(model):
            missing.append(model)
    return missing


def format_preflight_error(missing: list[str]) -> str:
    """Explain whether missing models need a CLI or API configuration."""
    cli_missing = []
    api_missing = []
    for model in missing:
        provider, _ = _model_provider(model)
        if provider in API_MODELS:
            api_missing.append(model)
        else:
            cli_missing.append(model)

    messages = []
    if cli_missing:
        messages.append(f"Error: Missing CLI tools: {', '.join(cli_missing)}")
    if api_missing:
        names = ", ".join(api_missing)
        messages.append(
            f"Error: Missing API configuration: {names}. "
            "Set OPENAI_API_KEY in the environment."
        )
    return "\\n".join(messages)


async def _run_openai(prompt: str, timeout: int, model_name: str | None = None) -> str:
    """Invoke OpenAI's chat completions API without requiring an SDK."""
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is not set")
    model_name = model_name or os.environ.get("OPENAI_MODEL", OPENAI_MODEL)
    payload = json.dumps({
        "model": model_name,
        "messages": [{"role": "user", "content": prompt}],
    }).encode()
    request = urllib.request.Request(
        os.environ.get("OPENAI_API_URL", OPENAI_API_URL),
        data=payload,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )

    def request_api() -> str:
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                data = json.loads(response.read())
        except urllib.error.HTTPError as error:
            detail = error.read().decode(errors="replace")
            raise RuntimeError(f"OpenAI API returned HTTP {error.code}: {detail}") from error
        except urllib.error.URLError as error:
            raise RuntimeError(f"OpenAI API request failed: {error.reason}") from error
        try:
            return data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as error:
            raise RuntimeError("OpenAI API response did not contain message content") from error

    # Do not use asyncio.to_thread here.  Its work runs in the event loop's
    # default executor, and asyncio.run() waits for that executor to shut down
    # after cancellation.  A cancelled timeout would therefore still block
    # until urlopen() returned.  A daemon thread lets the coroutine cancel
    # immediately while the socket-level timeout bounds the abandoned request.
    loop = asyncio.get_running_loop()
    result: asyncio.Future[str] = loop.create_future()

    def complete_request() -> None:
        try:
            value = request_api()
        except BaseException as error:
            callback = lambda: set_result(error=error)
        else:
            callback = lambda: set_result(value=value)
        try:
            loop.call_soon_threadsafe(callback)
        except RuntimeError:
            # The event loop may have closed after the caller cancelled us.
            pass

    def set_result(*, value: str | None = None, error: BaseException | None = None) -> None:
        if result.done():
            return
        if error is not None:
            result.set_exception(error)
        else:
            result.set_result(value)  # type: ignore[arg-type]

    threading.Thread(target=complete_request, daemon=True).start()
    return await asyncio.wait_for(result, timeout=timeout)


async def run_model(model: str, prompt: str, timeout: int = DEFAULT_TIMEOUT) -> str:
    """
    Invoke a model, using an API for API-backed providers or a CLI over stdin.

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
    provider, requested_model = _model_provider(model)
    if provider in API_MODELS:
        try:
            return await _run_openai(prompt, timeout, requested_model)
        except asyncio.TimeoutError:
            raise TimeoutError(f"Model {model} timed out after {timeout}s") from None

    if provider not in MODEL_COMMANDS or model != provider:
        available = [*MODEL_COMMANDS.keys(), *API_MODELS]
        raise ValueError(f"Unknown model: {model}. Available: {available}")

    cmd = MODEL_COMMANDS[provider]

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
    timeout: int | None = None,
) -> ModelReview:
    """
    Run review with a single model.

    Args:
        model: Model name
        prompt: Review prompt
        observations: Optional observation results to include in review
        timeout: Timeout in seconds (default: DEFAULT_TIMEOUT)

    Returns:
        Parsed ModelReview
    """
    try:
        response = await run_model(model, prompt, timeout=timeout or DEFAULT_TIMEOUT)
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
    timeout: int | None = None,
) -> list[ModelReview]:
    """
    Run review with multiple models concurrently.

    Args:
        models: List of model names
        prompt: Review prompt (same for all models)
        observations: Optional observation results to include
        timeout: Timeout in seconds per model (default: DEFAULT_TIMEOUT)

    Returns:
        List of ModelReview, one per model
    """
    tasks = [
        review_with_model(model, prompt, observations, timeout=timeout)
        for model in models
    ]
    return await asyncio.gather(*tasks)
