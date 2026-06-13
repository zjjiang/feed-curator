from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class FetchedItem:
    external_id: str
    title: str
    url: str
    author: str | None = None
    description: str | None = None
    content_text: str | None = None
    content_html: str | None = None
    cover_image_url: str | None = None
    published_at: int | None = None
    meta: dict[str, Any] = field(default_factory=dict)


class SourceAdapter(ABC):
    type: str = ""

    @abstractmethod
    def fetch(self, config: dict[str, Any]) -> list[FetchedItem]:
        ...
