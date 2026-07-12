"""safety: per-rule verdicts, different model family, temperature 0.

docs/product.md "Safety" enforcement: the safety node verdicts each story
per rule at temperature 0; any fail routes to revise; two fails reject.
The cross-family invariant is enforced by Settings at load time — a shared
writer/judge blind spot is the failure mode that matters.
"""

from pydantic_ai import Agent
from pydantic_ai.models import Model
from pydantic_ai.settings import ModelSettings

from src.config import Settings
from src.pipeline.cache import ArtifactCache, run_step
from src.pipeline.models import SafetyReport, Story
from src.pipeline.providers import build_model

# Bump when the instructions change: prompt text is a cache-key input by proxy.
PROMPT_VERSION = 1

SAFETY_TEMPERATURE = 0.0
SAFETY_MODEL_SETTINGS = ModelSettings(temperature=SAFETY_TEMPERATURE)

# The nine rules and their meanings, verbatim from docs/product.md "Safety".
SAFETY_INSTRUCTIONS = """\
You are a strict safety judge for bedtime stories aimed at pre-readers aged
3-6. Judge the story you are given against each of these nine rules and
return one verdict per rule — every rule, exactly once, with a short reason:

- mildest_peril_only: no violence beyond the mildest peril.
- no_fear_reinforcement: no darkness-as-threat, monsters, abandonment, or injury.
- no_brands: no brands or licensed characters.
- no_romance: no romance.
- kindness_resolves: kind, inclusive characters; resolution through help,
  never punishment.
- within_limits: vocabulary suits ages 3-6; 10 pages, 30-70 words per page,
  250-600 words total, no sentence over 20 words.
- right_language: the story language matches the language declared in the
  story data.
- calm_pictures: any image descriptions contain no text and nothing frightening.
- nothing_real: no real people, real places presented as real, or religious
  instruction.

Judge only what is on the page. Do not extend goodwill: a rule passes only
when the story clearly satisfies it.
"""


def build_safety_agent(model: Model) -> Agent[None, SafetyReport]:
    return Agent(
        model=model,
        output_type=SafetyReport,
        instructions=SAFETY_INSTRUCTIONS,
        model_settings=SAFETY_MODEL_SETTINGS,
    )


def safety_gate(
    story: Story,
    settings: Settings,
    cache: ArtifactCache,
    *,
    model: Model | None = None,
) -> SafetyReport:
    """Verdict all nine rules for this exact story; unchanged story, zero calls."""
    llm = model if model is not None else build_model(settings.safety_model, settings)
    inputs = {
        "story": story.model_dump(mode="json"),
        "model": settings.safety_model,
        "temperature": SAFETY_TEMPERATURE,
        "prompt_version": PROMPT_VERSION,
    }

    def produce() -> bytes:
        report = build_safety_agent(llm).run_sync(story.model_dump_json()).output
        return report.model_dump_json().encode()

    return SafetyReport.model_validate_json(run_step(cache, "safety", inputs, produce))
