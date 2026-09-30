from pydantic import BaseModel, Field


class SessionCreate(BaseModel):
    name: str = ""
    frequencies: list[int] = Field(..., description="场次可用整数频点列表")


class GroupIn(BaseModel):
    id: str = Field(..., min_length=1)
    start: int = Field(..., description="时段起点（含）")
    end: int = Field(..., description="时段终点（不含）")


class GroupsReplace(BaseModel):
    groups: list[GroupIn]


class InterferenceIn(BaseModel):
    a: str
    b: str
    min_separation: int = Field(..., description="最小频点间距，正整数")


class InterferencesReplace(BaseModel):
    interferences: list[InterferenceIn]


class PublishIn(BaseModel):
    base_version: int = Field(..., description="调用方所见的当前版本号")
    locks: dict[str, int] = Field(default_factory=dict,
                                  description="锁定组频点 {组编号: 频点}")
