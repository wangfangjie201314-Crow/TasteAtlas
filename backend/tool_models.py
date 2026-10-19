from pydantic import BaseModel, Field, field_validator


class ResolveRequest(BaseModel):
    model_config={'extra':'forbid'}
    city: str = Field(min_length=2,max_length=80)
    favorite: str = Field(min_length=2,max_length=120)
    @field_validator('city','favorite',mode='before')
    @classmethod
    def strip_text(cls,value):
        return value.strip() if isinstance(value,str) else value


class ProfileRequest(BaseModel):
    model_config={'extra':'forbid'}
    profile_ref: str = Field(min_length=16,max_length=128)


class GuidedPlanRequest(ProfileRequest):
    entity_id: str = Field(min_length=1,max_length=128)
