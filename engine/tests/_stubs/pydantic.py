"""Minimal offline stand-in for pydantic (only what midas_engine.settings
uses: BaseModel with defaults, Field(default_factory=...), model_validate_json,
model_validate, model_dump, model_dump_json)."""
import copy
import json


class _FieldInfo:
    def __init__(self, default=None, default_factory=None, **_kw):
        self.default = default
        self.default_factory = default_factory


def Field(default=None, default_factory=None, **kw):
    return _FieldInfo(default, default_factory, **kw)


class BaseModel:
    def __init__(self, **data):
        hints = {}
        for klass in reversed(type(self).__mro__):
            hints.update(getattr(klass, "__annotations__", {}))
        for name in hints:
            if name.startswith("_"):
                continue
            if name in data:
                value = data[name]
            else:
                default = getattr(type(self), name, None)
                if isinstance(default, _FieldInfo):
                    value = (default.default_factory()
                             if default.default_factory else default.default)
                else:
                    value = copy.deepcopy(default)
            setattr(self, name, value)

    @classmethod
    def model_validate_json(cls, raw):
        data = json.loads(raw)
        if not isinstance(data, dict):
            raise ValueError("settings JSON must be an object")
        return cls(**data)

    @classmethod
    def model_validate(cls, data):
        return cls(**dict(data))

    def _field_names(self):
        hints = {}
        for klass in reversed(type(self).__mro__):
            hints.update(getattr(klass, "__annotations__", {}))
        return [n for n in hints if not n.startswith("_")]

    def model_dump(self):
        return {n: getattr(self, n) for n in self._field_names()}

    def model_dump_json(self, **_kw):
        return json.dumps(self.model_dump(), default=str)
