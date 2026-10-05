"""Menu schemas.

`display_price_paise` / `display_price_delta_paise` are menu *information only* (₹1 = 100 paise).
The platform never totals, bills, collects or reports money.
"""

from __future__ import annotations

import uuid
from typing import Annotated, Literal

from pydantic import Field, model_validator

from app.schemas.common import (
    RequestModel,
    ResponseModel,
    UUIDStr,
    multiline_text,
    plain_text,
)

CategoryName = plain_text(1, 60)
ItemName = plain_text(1, 80)
GroupName = plain_text(1, 60)
OptionName = plain_text(1, 60)
CategoryDescription = multiline_text(300)
ItemDescription = multiline_text(500)
SortOrder = Annotated[int, Field(ge=0, le=100000)]
Dietary = Literal["veg", "non_veg", "egg"]
DisplayPrice = Annotated[int, Field(ge=0, le=100_000_000, description="Informational only.")]
DisplayPriceDelta = Annotated[
    int, Field(ge=-100_000_000, le=100_000_000, description="Informational only.")
]
SelectCount = Annotated[int, Field(ge=0, le=20)]


# --- requests ----------------------------------------------------------------------------------


class CreateCategoryRequest(RequestModel):
    name: CategoryName  # type: ignore[valid-type]
    description: CategoryDescription | None = None  # type: ignore[valid-type]
    sort_order: SortOrder | None = None  # omitted ⇒ placed after existing entries
    is_active: bool = True


class UpdateCategoryRequest(RequestModel):
    name: CategoryName | None = None  # type: ignore[valid-type]
    description: CategoryDescription | None = None  # type: ignore[valid-type]
    sort_order: SortOrder | None = None
    is_active: bool | None = None


class CreateItemRequest(RequestModel):
    category_id: UUIDStr
    name: ItemName  # type: ignore[valid-type]
    description: ItemDescription | None = None  # type: ignore[valid-type]
    dietary: Dietary | None = None
    display_price_paise: DisplayPrice | None = None
    sort_order: SortOrder | None = None  # omitted ⇒ placed after existing entries
    is_active: bool = True
    is_available: bool = True


class UpdateItemRequest(RequestModel):
    category_id: UUIDStr | None = None
    name: ItemName | None = None  # type: ignore[valid-type]
    description: ItemDescription | None = None  # type: ignore[valid-type]
    dietary: Dietary | None = None
    display_price_paise: DisplayPrice | None = None
    sort_order: SortOrder | None = None
    is_active: bool | None = None
    is_available: bool | None = None


class AvailabilityRequest(RequestModel):
    is_available: bool


class ItemModifierGroupsRequest(RequestModel):
    group_ids: Annotated[list[UUIDStr], Field(max_length=20)]

    @model_validator(mode="after")
    def _unique(self) -> ItemModifierGroupsRequest:
        if len(set(self.group_ids)) != len(self.group_ids):
            raise ValueError("group_ids must not contain duplicates")
        return self


class CreateModifierGroupRequest(RequestModel):
    name: GroupName  # type: ignore[valid-type]
    min_select: SelectCount = 0
    max_select: Annotated[int, Field(ge=1, le=20)] = 1
    sort_order: SortOrder | None = None  # omitted ⇒ placed after existing entries

    @model_validator(mode="after")
    def _range(self) -> CreateModifierGroupRequest:
        if self.min_select > self.max_select:
            raise ValueError("min_select cannot exceed max_select")
        return self


class UpdateModifierGroupRequest(RequestModel):
    name: GroupName | None = None  # type: ignore[valid-type]
    min_select: SelectCount | None = None
    max_select: Annotated[int, Field(ge=1, le=20)] | None = None
    sort_order: SortOrder | None = None


class CreateModifierRequest(RequestModel):
    name: OptionName  # type: ignore[valid-type]
    display_price_delta_paise: DisplayPriceDelta | None = None
    sort_order: SortOrder | None = None  # omitted ⇒ placed after existing entries
    is_available: bool = True


class UpdateModifierRequest(RequestModel):
    name: OptionName | None = None  # type: ignore[valid-type]
    display_price_delta_paise: DisplayPriceDelta | None = None
    sort_order: SortOrder | None = None
    is_available: bool | None = None


# --- responses ---------------------------------------------------------------------------------


class ModifierResponse(ResponseModel):
    id: uuid.UUID
    group_id: uuid.UUID
    name: str
    display_price_delta_paise: int | None
    is_available: bool
    sort_order: int


class ModifierGroupResponse(ResponseModel):
    id: uuid.UUID
    name: str
    min_select: int
    max_select: int
    sort_order: int
    modifiers: list[ModifierResponse] = []


class ItemResponse(ResponseModel):
    id: uuid.UUID
    category_id: uuid.UUID
    name: str
    description: str | None
    dietary: str | None
    display_price_paise: int | None
    image_url: str | None
    thumbnail_url: str | None
    is_active: bool
    is_available: bool
    sort_order: int
    modifier_group_ids: list[uuid.UUID]


class CategoryResponse(ResponseModel):
    id: uuid.UUID
    name: str
    description: str | None
    sort_order: int
    is_active: bool


class MenuCategoryResponse(CategoryResponse):
    items: list[ItemResponse]


class StaffMenuResponse(ResponseModel):
    categories: list[MenuCategoryResponse]
    modifier_groups: list[ModifierGroupResponse]


# Customer view: only what a diner needs, compact for weak 4G.


class PublicOption(ResponseModel):
    id: uuid.UUID
    name: str
    display_price_delta_paise: int | None
    is_available: bool


class PublicGroup(ResponseModel):
    id: uuid.UUID
    name: str
    min_select: int
    max_select: int
    options: list[PublicOption]


class PublicItem(ResponseModel):
    id: uuid.UUID
    name: str
    description: str | None
    dietary: str | None
    display_price_paise: int | None
    image_url: str | None
    thumbnail_url: str | None
    is_available: bool
    orderable: bool
    modifier_group_ids: list[uuid.UUID]


class PublicCategory(ResponseModel):
    id: uuid.UUID
    name: str
    description: str | None
    items: list[PublicItem]


class OrderingMenuResponse(ResponseModel):
    """What can be ordered right now (used by the waiter app; same shape as diners see)."""

    categories: list[PublicCategory]
    modifier_groups: list[PublicGroup]


class PublicMenuResponse(ResponseModel):
    restaurant_name: str
    table_label: str
    categories: list[PublicCategory]
    modifier_groups: list[PublicGroup]
