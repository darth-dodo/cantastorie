"""Initial story.json models: structure only.

Content-rule validation (word counts, sentence caps) lands in assemble (AI-361).
"""

from typing import Literal, Self

from pydantic import BaseModel, Field, model_validator

Language = Literal["it", "es", "en", "el", "de"]

Theme = Literal[
    "animals_helping_each_other",
    "tiny_garden_adventure",
    "the_sleepy_sea",
    "rain_and_puddles",
    "bakery_morning",
    "grandparent_visit",
    "the_lost_mitten",
    "gentle_forest_friends",
    "the_moon_says_goodnight",
    "picnic_surprise",
    "the_little_boat",
    "first_snow",
]

SafetyRule = Literal[
    "mildest_peril_only",
    "no_fear_reinforcement",
    "no_brands",
    "no_romance",
    "kindness_resolves",
    "within_limits",
    "right_language",
    "calm_pictures",
    "nothing_real",
]

SAFETY_RULES: tuple[SafetyRule, ...] = (
    "mildest_peril_only",
    "no_fear_reinforcement",
    "no_brands",
    "no_romance",
    "kindness_resolves",
    "within_limits",
    "right_language",
    "calm_pictures",
    "nothing_real",
)


class WordTiming(BaseModel):
    word: str
    start_s: float = Field(ge=0)
    end_s: float = Field(ge=0)

    @model_validator(mode="after")
    def end_not_before_start(self) -> Self:
        if self.end_s < self.start_s:
            raise ValueError("end_s must be >= start_s")
        return self


class PageAudio(BaseModel):
    file: str
    timings: list[WordTiming] = Field(default_factory=list)


class ChoiceOption(BaseModel):
    label: str  # story text: counts toward every limit and the gloss map
    card_image: str | None = None
    next_page: str


class ChoicePoint(BaseModel):
    options: tuple[ChoiceOption, ChoiceOption]


class Page(BaseModel):
    id: str
    text: str
    audio: PageAudio | None = None
    image: str | None = None
    next_page: str | None = None
    choice: ChoicePoint | None = None


class Story(BaseModel):
    schema_version: int = 1
    id: str
    language: Language
    title: str
    theme: Theme
    shape: Literal["linear", "branching"]
    pages: list[Page] = Field(min_length=1)
    gloss: dict[str, str] | None = None  # None for English stories


class SafetyVerdict(BaseModel):
    rule: SafetyRule
    passed: bool
    reason: str


class SafetyReport(BaseModel):
    verdicts: list[SafetyVerdict]

    @model_validator(mode="after")
    def covers_all_rules_exactly_once(self) -> Self:
        seen = [verdict.rule for verdict in self.verdicts]
        if sorted(seen) != sorted(SAFETY_RULES):
            raise ValueError("safety report must contain each of the nine rules exactly once")
        return self

    @property
    def passed(self) -> bool:
        return all(verdict.passed for verdict in self.verdicts)
