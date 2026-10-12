import re
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class MaintenanceResponse:
    request_id: int
    response_type: str
    fields: dict[str, str]
    raw_line: str

    @property
    def ok(self) -> bool:
        return self.fields.get("ok") == "1"


class FirmwareProtocolParser:
    _field_pattern = re.compile(r"([A-Za-z_]+)=([^\s]+)")

    @classmethod
    def parse_maintenance_response(cls, line: str) -> MaintenanceResponse | None:
        text = line.strip()
        if not text.startswith("maintenance:"):
            return None
        fields = dict(cls._field_pattern.findall(text[len("maintenance:"):]))
        try:
            request_id = int(fields["id"])
            response_type = fields["type"]
        except (KeyError, ValueError):
            return None
        if request_id <= 0 or not response_type:
            return None
        return MaintenanceResponse(request_id, response_type, fields, text)