"""Small, dependency-free reader/editor for selected Cyberpunk sav.dat fields."""

from __future__ import annotations

import struct
from dataclasses import dataclass
from pathlib import Path


HEADER_SIZE = 25
DIRECTORY_MAGIC = b"FZLC"
# The current sav.dat chunk marker is the literal four-byte sequence ``4ZLX``.
# It is easy to misread as ``FZLX`` in format notes because it is a packed
# signature; validate the bytes as stored instead of assuming the first byte.
CHUNK_MAGIC = b"4ZLX"
NODE_MAGIC = b"EDON"
FOOTER_MAGIC = b"ENOD"
NO_NODE = 0xFFFFFFFF


class SaveFormatError(ValueError):
    """Raised when a save container is malformed or unsupported."""


@dataclass(frozen=True)
class Chunk:
    offset: int
    compressed_size: int
    uncompressed_size: int


@dataclass(frozen=True)
class Node:
    name: str
    next_index: int
    child_index: int
    offset: int
    size: int
    payload_available: bool


@dataclass(frozen=True)
class CharacterSnapshot:
    """Fields decoded from the selected player's development-system record."""

    values: dict[str, int]
    locations: dict[str, int]


def decompress_lz4_block(source: bytes, expected_size: int) -> bytes:
    """Decode an LZ4 block using the raw block format stored by sav.dat."""
    out = bytearray()
    cursor = 0
    while cursor < len(source):
        token = source[cursor]
        cursor += 1

        literal_length = token >> 4
        if literal_length == 15:
            while True:
                if cursor >= len(source):
                    raise SaveFormatError("Truncated LZ4 literal length")
                extension = source[cursor]
                cursor += 1
                literal_length += extension
                if extension != 255:
                    break

        literal_end = cursor + literal_length
        if literal_end > len(source):
            raise SaveFormatError("LZ4 literal overruns its chunk")
        out.extend(source[cursor:literal_end])
        cursor = literal_end
        if cursor == len(source):
            break

        if cursor + 2 > len(source):
            raise SaveFormatError("Truncated LZ4 match offset")
        match_offset = source[cursor] | (source[cursor + 1] << 8)
        cursor += 2
        if match_offset == 0 or match_offset > len(out):
            raise SaveFormatError("Invalid LZ4 match offset")

        match_length = token & 0x0F
        if match_length == 15:
            while True:
                if cursor >= len(source):
                    raise SaveFormatError("Truncated LZ4 match length")
                extension = source[cursor]
                cursor += 1
                match_length += extension
                if extension != 255:
                    break
        match_length += 4
        for _ in range(match_length):
            out.append(out[-match_offset])
            if len(out) > expected_size:
                raise SaveFormatError("LZ4 output exceeds its declared size")

    if len(out) != expected_size:
        raise SaveFormatError(f"LZ4 output size {len(out)} does not match {expected_size}")
    return bytes(out)


def _read_packed_int(data: bytes, cursor: int) -> tuple[int, int]:
    value = shift = 0
    while True:
        if cursor >= len(data):
            raise SaveFormatError("Truncated packed integer in node table")
        byte = data[cursor]
        cursor += 1
        value |= (byte & 0x3F) << shift
        shift += 6
        if not (byte & 0x40):
            return value, cursor
        if shift > 60:
            raise SaveFormatError("Packed integer is too large")


class SaveContainer:
    """Validated decompressed view of a sav.dat container."""

    def __init__(self, path: Path):
        self.path = Path(path)
        try:
            self.file_bytes = self.path.read_bytes()
        except OSError as exc:
            raise SaveFormatError(str(exc)) from exc
        self._parse()

    def _parse(self) -> None:
        data = self.file_bytes
        if len(data) < HEADER_SIZE + 12 or data[:4] != b"VASC":
            raise SaveFormatError("Unrecognized or incomplete sav.dat header")
        self.header = data[:HEADER_SIZE]
        if data[HEADER_SIZE:HEADER_SIZE + 4] != DIRECTORY_MAGIC:
            raise SaveFormatError("Chunk directory marker is missing")
        count = struct.unpack_from("<I", data, HEADER_SIZE + 4)[0]
        directory_end = HEADER_SIZE + 8 + count * 12
        if count == 0 or count > 4096 or directory_end > len(data) - 8:
            raise SaveFormatError("Invalid chunk directory size")

        self.chunks = tuple(
            Chunk(*struct.unpack_from("<III", data, HEADER_SIZE + 8 + i * 12))
            for i in range(count)
        )
        decoded = []
        for chunk in self.chunks:
            end = chunk.offset + chunk.compressed_size
            if chunk.offset < directory_end or end > len(data) - 8 or chunk.compressed_size < 8:
                raise SaveFormatError("Chunk range falls outside the file")
            if data[chunk.offset:chunk.offset + 4] != CHUNK_MAGIC:
                raise SaveFormatError("Chunk marker is invalid")
            declared = struct.unpack_from("<I", data, chunk.offset + 4)[0]
            if declared != chunk.uncompressed_size:
                raise SaveFormatError("Chunk size disagrees with its directory entry")
            decoded.append(decompress_lz4_block(
                data[chunk.offset + 8:end], chunk.uncompressed_size))
        self.payload = b"".join(decoded)
        # Node offsets include the header and chunk-directory prefix, while
        # ``payload`` starts at the first decompressed chunk.
        self.data_prefix = self.chunks[0].offset

        if data[-4:] != FOOTER_MAGIC:
            raise SaveFormatError("Node table footer is missing")
        table_offset = struct.unpack_from("<I", data, len(data) - 8)[0]
        if table_offset >= len(data) - 8 or data[table_offset:table_offset + 4] != NODE_MAGIC:
            raise SaveFormatError("Node table location is invalid")
        self.node_table_offset = table_offset
        cursor = table_offset + 4
        node_count, cursor = _read_packed_int(data, cursor)
        if node_count > 1_000_000:
            raise SaveFormatError("Implausible node count")

        nodes = []
        for _ in range(node_count):
            if cursor >= len(data) - 8:
                raise SaveFormatError("Truncated node descriptor")
            name_length = data[cursor] & 0x7F
            cursor += 1
            name_end = cursor + name_length
            if name_end + 16 > len(data) - 8:
                raise SaveFormatError("Node descriptor extends past the table")
            try:
                name = data[cursor:name_end].decode("utf-8")
            except UnicodeDecodeError as exc:
                raise SaveFormatError("Node name is not valid UTF-8") from exc
            cursor = name_end
            next_index, child_index, offset, size = struct.unpack_from("<IIII", data, cursor)
            cursor += 16
            for link in (next_index, child_index):
                if link != NO_NODE and link >= node_count:
                    raise SaveFormatError("Node link points outside the node table")
            payload_offset = offset - self.data_prefix
            available = payload_offset >= 0 and payload_offset <= len(self.payload) and size <= len(self.payload) - payload_offset
            nodes.append(Node(name, next_index, child_index, offset, size, available))

        if cursor != len(data) - 8:
            raise SaveFormatError("Unexpected data after the node table")
        self.nodes = tuple(nodes)
        self.nodes_by_name = {node.name: node for node in nodes}

    def node_payload(self, name: str) -> bytes:
        """Return a node's serialized bytes without interpreting or changing them."""
        try:
            node = self.nodes_by_name[name]
        except KeyError as exc:
            raise KeyError(f"Node not found: {name}") from exc
        if not node.payload_available:
            raise SaveFormatError(
                f"Node {name} points outside decompressed chunks; its payload layout is unsupported")
        start = node.offset - self.data_prefix
        return self.payload[start:start + node.size]

    @staticmethod
    def _struct_fields(raw: bytes, start: int, strings: list[str]) -> list[tuple[str, str, int]]:
        if start + 2 > len(raw):
            raise SaveFormatError("Truncated serialized struct")
        count = struct.unpack_from("<H", raw, start)[0]
        if count > 4096 or start + 2 + count * 8 > len(raw):
            raise SaveFormatError("Invalid serialized struct field table")
        fields = []
        for index in range(count):
            name_id, type_id, offset = struct.unpack_from("<HHI", raw, start + 2 + index * 8)
            if name_id >= len(strings) or type_id >= len(strings):
                raise SaveFormatError("Serialized field refers to an unknown string")
            fields.append((strings[name_id], strings[type_id], offset))
        return fields

    @staticmethod
    def _scalar_width(type_name: str) -> int | None:
        if type_name in ("Int32", "Uint32", "Float"):
            return 4
        if type_name == "Bool":
            return 1
        if type_name == "TweakDBID":
            return 8
        if type_name.startswith("gamedata"):
            return 2
        return None

    def _scriptable_structs(self) -> tuple[list[str], list[tuple[str, bytes, int]]]:
        node = self.nodes_by_name.get("ScriptableSystemsContainer")
        if node is None or not node.payload_available:
            raise SaveFormatError("Player development data is unavailable in this save")
        raw = self.node_payload(node.name)
        if len(raw) < 32 or struct.unpack_from("<I", raw, 4)[0] != len(raw) - 8:
            raise SaveFormatError("Unsupported scriptable-systems container")
        string_start, string_end = struct.unpack_from("<II", raw, 16)
        data_start, data_end = struct.unpack_from("<II", raw, 24)
        base = 32
        if struct.unpack_from("<I", raw, 12)[0] >= 2:
            if base + 4 > len(raw):
                raise SaveFormatError("Truncated scriptable-systems extension")
            extension_count = struct.unpack_from("<I", raw, base)[0]
            if extension_count > 4096:
                raise SaveFormatError("Invalid scriptable-systems extension")
            base += 4 + 8 * extension_count
        if base > len(raw):
            raise SaveFormatError("Invalid scriptable-systems base offset")
        body = raw[base:]
        if string_start != 0 or string_end < string_start or string_end % 4:
            raise SaveFormatError("Unsupported string index in scriptable-systems container")
        strings: list[str] = []
        cursor = string_end
        for position in range(string_start, string_end, 4):
            if position + 4 > len(body):
                raise SaveFormatError("Truncated scriptable-systems string index")
            packed = struct.unpack_from("<I", body, position)[0]
            if (packed & 0xFFFFFF) != cursor:
                raise SaveFormatError("Invalid scriptable-systems string offset")
            length = packed >> 24
            if length < 1 or cursor + length > len(body) or body[cursor + length - 1] != 0:
                raise SaveFormatError("Invalid scriptable-systems string")
            try:
                strings.append(body[cursor:cursor + length - 1].decode("utf-8"))
            except UnicodeDecodeError as exc:
                raise SaveFormatError("Invalid scriptable-systems string encoding") from exc
            cursor += length
        if data_start != cursor or data_end < data_start or (data_end - data_start) % 8:
            raise SaveFormatError("Invalid scriptable-systems object index")
        descriptors = [struct.unpack_from("<II", body, p) for p in range(data_start, data_end, 8)]
        objects = []
        for index, (name_id, offset) in enumerate(descriptors):
            if name_id >= len(strings) or offset > len(body):
                raise SaveFormatError("Invalid scriptable-systems object descriptor")
            end = descriptors[index + 1][1] if index + 1 < len(descriptors) else len(body)
            if end < offset:
                raise SaveFormatError("Overlapping scriptable-systems objects")
            objects.append((strings[name_id], body[offset:end], base + offset))
        return strings, objects

    def character_snapshot(self) -> CharacterSnapshot:
        """Decode known player attributes and progression levels, without heuristics."""
        strings, objects = self._scriptable_structs()
        system_index = next((i for i, item in enumerate(objects) if item[0] == "PlayerDevelopmentSystem"), None)
        if system_index is None:
            raise SaveFormatError("Player development system was not found")
        _name, system_raw, system_node_offset = objects[system_index]
        system_fields = self._struct_fields(system_raw, 0, strings)
        player_data_field = next((field for field in system_fields if field[0] == "playerData"), None)
        if player_data_field is None or player_data_field[1] != "array:handle:PlayerDevelopmentData":
            raise SaveFormatError("Player development data has an unsupported layout")
        player_data_offset = player_data_field[2]
        if player_data_offset + 4 > len(system_raw):
            raise SaveFormatError("Player data handle array is truncated")
        handle_count = struct.unpack_from("<I", system_raw, player_data_offset)[0]
        if handle_count < 1 or handle_count > 4096 or player_data_offset + 4 + 4 * handle_count > len(system_raw):
            raise SaveFormatError("Player data handle array is invalid")
        handles = struct.unpack_from(f"<{handle_count}I", system_raw, player_data_offset + 4)
        candidates = []
        for handle in handles:
            if handle >= len(objects) or objects[handle][0] != "PlayerDevelopmentData":
                continue
            candidates.append(objects[handle])
        if not candidates:
            raise SaveFormatError("Player development record was not found")

        # The player record is the candidate with the standard five attributes
        # and both player-facing proficiency markers; NPC records do not match.
        selected = None
        for _object_name, object_raw, object_offset in candidates:
            object_fields = self._struct_fields(object_raw, 0, strings)
            by_name = {name: (type_name, offset) for name, type_name, offset in object_fields}
            arrays = {}
            for array_name in ("attributes", "proficiencies", "devPoints"):
                field = by_name.get(array_name)
                if not field or not field[0].startswith("array:"):
                    continue
                type_name, offset = field
                if offset + 4 > len(object_raw):
                    continue
                count = struct.unpack_from("<I", object_raw, offset)[0]
                if count > 4096:
                    continue
                position = offset + 4
                records = []
                valid = True
                for _ in range(count):
                    try:
                        row_fields = self._struct_fields(object_raw, position, strings)
                    except SaveFormatError:
                        valid = False
                        break
                    if not row_fields:
                        valid = False
                        break
                    sizes = [2 + 8 * len(row_fields)]
                    for _field_name, field_type, field_offset in row_fields:
                        width = self._scalar_width(field_type)
                        if width:
                            sizes.append(field_offset + width)
                    row_size = max(sizes)
                    if position + row_size > len(object_raw):
                        valid = False
                        break
                    records.append((position, row_fields, row_size))
                    position += row_size
                if valid:
                    arrays[array_name] = records
            if "attributes" not in arrays or "proficiencies" not in arrays:
                continue
            attribute_names = set()
            proficiency_names = set()
            for position, fields, _size in arrays["attributes"]:
                for fname, ftype, offset in fields:
                    if fname == "attributeName" and ftype.startswith("gamedata"):
                        value = struct.unpack_from("<H", object_raw, position + offset)[0]
                        if value < len(strings):
                            attribute_names.add(strings[value])
            for position, fields, _size in arrays["proficiencies"]:
                for fname, ftype, offset in fields:
                    if fname == "type" and ftype.startswith("gamedata"):
                        value = struct.unpack_from("<H", object_raw, position + offset)[0]
                        if value < len(strings):
                            proficiency_names.add(strings[value])
            if {"Cool", "Intelligence", "Reflexes", "Strength", "TechnicalAbility"} <= attribute_names and {"Level", "StreetCred"} <= proficiency_names:
                selected = (object_raw, object_offset, by_name, arrays)
                break
        if selected is None:
            raise SaveFormatError("Could not safely distinguish the player record from NPC records")

        object_raw, object_offset, by_name, arrays = selected
        node = self.nodes_by_name["ScriptableSystemsContainer"]
        node_start = node.offset - self.data_prefix
        values: dict[str, int] = {}
        locations: dict[str, int] = {}
        attribute_map = {
            "Cool": "cool", "Intelligence": "intelligence", "Reflexes": "reflexes",
            "Strength": "strength", "TechnicalAbility": "technicalAbility",
        }
        for position, fields, _size in arrays["attributes"]:
            enum_name = None
            for fname, ftype, offset in fields:
                if fname == "attributeName" and ftype.startswith("gamedata"):
                    enum_id = struct.unpack_from("<H", object_raw, position + offset)[0]
                    enum_name = strings[enum_id] if enum_id < len(strings) else None
            key = attribute_map.get(enum_name)
            if key:
                value_field = next((f for f in fields if f[0] == "value" and f[1] == "Int32"), None)
                if value_field:
                    _fname, _type, offset = value_field
                    values[key] = struct.unpack_from("<i", object_raw, position + offset)[0]
                    locations[key] = node_start + object_offset + position + offset
        proficiency_map = {
            "Level": "level", "StreetCred": "streetCred",
            # Cyberpunk 2.0's five skill progressions are stored in the
            # corresponding attribute proficiency records.
            "CoolSkill": "headHunter", "IntelligenceSkill": "netrunner",
            "ReflexesSkill": "shinobi", "StrengthSkill": "solo",
            "TechnicalAbilitySkill": "engineeringProgression",
            # Older saves can predate those records; use the old Engineering
            # proficiency only as a compatibility fallback.
            "Engineering": "legacyEngineering",
        }
        for position, fields, _size in arrays["proficiencies"]:
            enum_name = None
            for fname, ftype, offset in fields:
                if fname == "type" and ftype.startswith("gamedata"):
                    enum_id = struct.unpack_from("<H", object_raw, position + offset)[0]
                    enum_name = strings[enum_id] if enum_id < len(strings) else None
            key = proficiency_map.get(enum_name)
            value_field = next((f for f in fields if f[0] == "currentLevel" and f[1] == "Int32"), None)
            if key and value_field:
                _fname, _type, offset = value_field
                values[key] = struct.unpack_from("<i", object_raw, position + offset)[0]
                locations[key] = node_start + object_offset + position + offset
        if "engineeringProgression" not in values and "legacyEngineering" in values:
            values["engineeringProgression"] = values["legacyEngineering"]
            locations["engineeringProgression"] = locations["legacyEngineering"]
        values.pop("legacyEngineering", None)
        locations.pop("legacyEngineering", None)

        generic_perk_points = None
        generic_pool_seen = False
        secondary_perk_points = None
        for position, fields, _size in arrays.get("devPoints", []):
            point_type = None
            unspent = next((field for field in fields if field[0] == "unspent" and field[1] == "Int32"), None)
            for fname, ftype, offset in fields:
                if fname == "type" and ftype.startswith("gamedata"):
                    type_id = struct.unpack_from("<H", object_raw, position + offset)[0]
                    point_type = strings[type_id] if type_id < len(strings) else None
            if point_type is None:
                generic_pool_seen = True
            if unspent is None:
                if point_type is None:
                    generic_perk_points = (0, None)
                elif point_type == "Primary":
                    values["attributePoints"] = 0
                continue
            _fname, _type, offset = unspent
            point_value = struct.unpack_from("<i", object_raw, position + offset)[0]
            point_location = node_start + object_offset + position + offset
            if point_type == "Primary":
                values["attributePoints"] = point_value
                locations["attributePoints"] = point_location
            elif point_type == "Secondary":
                secondary_perk_points = (point_value, point_location)
            elif point_type is None:
                generic_perk_points = (point_value, point_location)
        if generic_pool_seen and generic_perk_points is not None:
            values["perkPoints"] = generic_perk_points[0]
            if generic_perk_points[1] is not None:
                locations["perkPoints"] = generic_perk_points[1]
        elif secondary_perk_points is not None:
            values["perkPoints"], locations["perkPoints"] = secondary_perk_points

        # When a pool is at zero REDengine can omit its unspent Int32 member.
        # Keep the visible zero; the writer will materialize that member in the
        # serialized row before exporting a non-zero edited value.
        if "attributePoints" not in values:
            values["attributePoints"] = 0
        if "perkPoints" not in values:
            values["perkPoints"] = 0
        required = {"level", "streetCred", *attribute_map.values()}
        if not required <= values.keys() or not locations.keys() >= required:
            raise SaveFormatError("The player record is missing one or more known editable fields")
        return CharacterSnapshot(values, locations)

    def _expand_unspent_points(self, fields: set[str]):
        """Materialize omitted zero-valued point members in a player dev-point row."""
        node = self.nodes_by_name.get("ScriptableSystemsContainer")
        if node is None:
            raise SaveFormatError("Player development data is unavailable")
        node_start = node.offset - self.data_prefix
        raw = bytearray(self.node_payload(node.name))
        base = 32
        if struct.unpack_from("<I", raw, 12)[0] >= 2:
            base += 4 + 8 * struct.unpack_from("<I", raw, base)[0]
        body = raw[base:]
        data_start, data_end = struct.unpack_from("<II", raw, 24)
        strings, objects = self._scriptable_structs()
        names = {value: index for index, value in enumerate(strings)}
        if "unspent" not in names or "Int32" not in names:
            raise SaveFormatError("Save schema does not support point-pool edits")
        system_index = next((i for i, obj in enumerate(objects) if obj[0] == "PlayerDevelopmentSystem"), None)
        if system_index is None:
            raise SaveFormatError("Player development system was not found")
        system_fields = self._struct_fields(objects[system_index][1], 0, strings)
        handle_field = next((f for f in system_fields if f[0] == "playerData"), None)
        if not handle_field:
            raise SaveFormatError("Player data handle array was not found")
        handles_offset = handle_field[2]
        handle_count = struct.unpack_from("<I", objects[system_index][1], handles_offset)[0]
        handles = struct.unpack_from(f"<{handle_count}I", objects[system_index][1], handles_offset + 4)
        player_index = next((h for h in handles if h < len(objects) and objects[h][0] == "PlayerDevelopmentData"), None)
        if player_index is None:
            raise SaveFormatError("Player development record was not found")

        object_name, object_raw, object_offset = objects[player_index]
        object_fields = self._struct_fields(object_raw, 0, strings)
        point_field = next((f for f in object_fields if f[0] == "devPoints"), None)
        if not point_field:
            raise SaveFormatError("Development-point records were not found")
        _field_name, type_name, array_offset = point_field
        if not type_name.startswith("array:"):
            raise SaveFormatError("Development-point array has an unsupported type")
        later_offsets = [f[2] for f in object_fields if f[2] > array_offset]
        array_end = min(later_offsets, default=len(object_raw))
        old_array = object_raw[array_offset:array_end]
        count = struct.unpack_from("<I", old_array)[0]
        position = 4
        rows = []
        for _ in range(count):
            row_fields = self._struct_fields(old_array, position, strings)
            widths = [2 + 8 * len(row_fields)]
            point_type = None
            unspent_offset = None
            for name, field_type, offset in row_fields:
                width = self._scalar_width(field_type)
                if width:
                    widths.append(offset + width)
                if name == "type" and field_type.startswith("gamedata"):
                    type_id = struct.unpack_from("<H", old_array, position + offset)[0]
                    point_type = strings[type_id] if type_id < len(strings) else None
                elif name == "unspent" and field_type == "Int32":
                    unspent_offset = offset
            row_size = max(widths)
            rows.append((position, row_fields, row_size, point_type, unspent_offset))
            position += row_size
        if position != len(old_array):
            raise SaveFormatError("Development-point row boundaries could not be verified")

        has_generic = any(point_type is None for *_rest, point_type, _unspent in rows)
        edits_needed = set(fields)
        if "perkPoints" in edits_needed and not has_generic:
            perk_type = "Secondary"
        else:
            perk_type = None
        rebuilt_array = bytearray(struct.pack("<I", count))
        new_locations = {}
        changed = set()
        for position, row_fields, row_size, point_type, unspent_offset in rows:
            row_start_new = len(rebuilt_array)
            key = "attributePoints" if point_type == "Primary" else (
                "perkPoints" if point_type is None or point_type == perk_type else None)
            row = old_array[position:position + row_size]
            if key in edits_needed and unspent_offset is None:
                old_count = len(row_fields)
                old_header = 2 + 8 * old_count
                new_header = old_header + 8
                rebuilt_row = bytearray(new_header + (row_size - old_header) + 4)
                struct.pack_into("<H", rebuilt_row, 0, old_count + 1)
                for index, (name, field_type, offset) in enumerate(row_fields):
                    name_id, type_id, _old = struct.unpack_from("<HHI", row, 2 + 8 * index)
                    adjusted = offset + 8 if offset >= old_header else offset
                    struct.pack_into("<HHI", rebuilt_row, 2 + 8 * index, name_id, type_id, adjusted)
                field_offset = new_header + (row_size - old_header)
                struct.pack_into("<HHI", rebuilt_row, 2 + old_count * 8,
                                 names["unspent"], names["Int32"], field_offset)
                rebuilt_row[new_header:new_header + row_size - old_header] = row[old_header:row_size]
                struct.pack_into("<i", rebuilt_row, field_offset, 0)
                row = bytes(rebuilt_row)
                unspent_offset = field_offset
                changed.add(key)
            elif key in edits_needed and unspent_offset is not None:
                changed.add(key)
            if key in ("attributePoints", "perkPoints") and unspent_offset is not None:
                new_locations[key] = node_start + object_offset + array_offset + row_start_new + unspent_offset
            rebuilt_array.extend(row)
        if not edits_needed <= changed:
            missing = ", ".join(sorted(edits_needed - changed))
            raise SaveFormatError(f"Could not locate point pools to edit: {missing}")

        delta = len(rebuilt_array) - len(old_array)
        if delta == 0:
            return self.payload, self.nodes, new_locations
        object_new = bytearray(object_raw[:array_offset] + rebuilt_array + object_raw[array_end:])
        for index, (_name, _type, offset) in enumerate(object_fields):
            if offset >= array_end:
                struct.pack_into("<I", object_new, 2 + index * 8 + 4, offset + delta)

        object_end = objects[player_index + 1][2] if player_index + 1 < len(objects) else len(raw)
        object_start = object_offset
        node_new = bytearray(raw[:object_start] + object_new + raw[object_end:])
        struct.pack_into("<I", node_new, 4, len(node_new) - 8)
        # The object index table is before the object payload, so only later
        # object offsets need to move.
        for index in range(player_index + 1, len(objects)):
            offset_at = base + data_start + index * 8 + 4
            old_value = struct.unpack_from("<I", node_new, offset_at)[0]
            struct.pack_into("<I", node_new, offset_at, old_value + delta)
        new_payload = self.payload[:node_start] + node_new + self.payload[node_start + node.size:]
        changed_nodes = []
        target_end = node.offset + node.size
        for item in self.nodes:
            offset = item.offset + (delta if item.offset >= target_end else 0)
            size = item.size + delta if item.name == node.name else item.size
            changed_nodes.append(Node(item.name, item.next_index, item.child_index, offset, size, True))
        # Prefix-relative offsets are updated by the payload writer if the chunk count changes.
        return bytes(new_payload), tuple(changed_nodes), new_locations

    def money_location(self) -> int:
        """Return the payload offset of the verified Items.money Int32 quantity.

        The game's serialized item record is inside the inventory node. The item
        identifier occurs twice in a small record: the first occurrence is a
        related reference, and the second is followed by the quantity field.
        We only accept that exact two-reference shape and a nonnegative Int32.
        """
        node = self.nodes_by_name.get("inventory")
        if node is None or not node.payload_available:
            raise SaveFormatError("Inventory node is missing or unavailable")
        node_start = node.offset - self.data_prefix
        inventory = self.payload[node_start:node_start + node.size]
        item_id = bytes.fromhex("ec88e1f50b000000")  # Items.money TweakDBID
        positions = []
        cursor = 0
        while (found := inventory.find(item_id, cursor)) >= 0:
            positions.append(found)
            cursor = found + 1
        if len(positions) == 2 and positions[1] - positions[0] == 20:
            # Standard layout: one pair of references, followed by the wallet
            # quantity at the known field offset.
            quantity_in_node = positions[1] + 21
        elif len(positions) == 4 and all(
                positions[index + 1] - positions[index] == 20
                for index in (0, 2)):
            # Some 2.31/2.5 saves serialize a second Items.money pair. In the
            # duplicated layout we've inspected, its quantity is 10,000 while
            # the first pair's quantity is the live wallet balance. Require
            # that exact unambiguous signature; otherwise fail closed.
            candidate_offsets = [positions[index + 1] + 21 for index in (0, 2)]
            candidates = [struct.unpack_from("<i", inventory, offset)[0]
                          for offset in candidate_offsets
                          if offset >= 0 and offset + 4 <= len(inventory)]
            wallet_candidates = [offset for offset, amount in zip(candidate_offsets, candidates)
                                 if amount != 10_000]
            sentinel_count = sum(amount == 10_000 for amount in candidates)
            if len(candidates) != 2 or sentinel_count != 1 or len(wallet_candidates) != 1:
                raise SaveFormatError("Wallet records are ambiguous in this inventory layout")
            quantity_in_node = wallet_candidates[0]
        else:
            raise SaveFormatError("Could not uniquely identify the saved money item in inventory")

        if quantity_in_node + 4 > len(inventory):
            raise SaveFormatError("Money quantity falls outside inventory")
        quantity = struct.unpack_from("<i", inventory, quantity_in_node)[0]
        if quantity < 0:
            raise SaveFormatError("Saved money quantity is negative or unsupported")
        return node_start + quantity_in_node

    def money(self) -> int:
        """Read the saved money quantity from the validated item record."""
        return struct.unpack_from("<i", self.payload, self.money_location())[0]

    @staticmethod
    def _compress_literal_block(source: bytes) -> bytes:
        """Encode a valid raw LZ4 block containing a single literal run."""
        length = len(source)
        output = bytearray([min(length, 15) << 4])
        if length >= 15:
            remaining = length - 15
            while remaining >= 255:
                output.append(255)
                remaining -= 255
            output.append(remaining)
        output.extend(source)
        return bytes(output)

    @staticmethod
    def _write_packed_int(value: int) -> bytes:
        if value < 0:
            raise SaveFormatError("Negative packed integer in node table")
        result = bytearray()
        while value >= 0x40:
            result.append((value & 0x3F) | 0x40)
            value >>= 6
        result.append(value)
        return bytes(result)

    def _write_resized_payload_copy(self, destination: Path, payload: bytes, nodes: tuple[Node, ...]) -> None:
        """Re-chunk a save after a schema row grows to add an omitted field."""
        chunk_capacity = self.chunks[0].uncompressed_size
        segments = [payload[i:i + chunk_capacity] for i in range(0, len(payload), chunk_capacity)]
        if not segments:
            raise SaveFormatError("Cannot write an empty save payload")
        new_prefix = HEADER_SIZE + 8 + len(segments) * 12
        prefix_delta = new_prefix - self.data_prefix
        table = bytearray(NODE_MAGIC)
        table.extend(self._write_packed_int(len(nodes)))
        for node in nodes:
            name = node.name.encode("utf-8")
            if len(name) > 127:
                raise SaveFormatError("Node name is too long to rebuild safely")
            table.append(len(name))
            table.extend(name)
            table.extend(struct.pack("<IIII", node.next_index, node.child_index,
                                     node.offset + prefix_delta, node.size))
        out = bytearray(self.header)
        out.extend(DIRECTORY_MAGIC)
        out.extend(struct.pack("<I", len(segments)))
        directory_start = len(out)
        out.extend(bytes(len(segments) * 12))
        entries = []
        first_changed_byte = next((i for i, (before, after) in enumerate(zip(self.payload, payload))
                                   if before != after), min(len(self.payload), len(payload)))
        changed_from = max(0, first_changed_byte // chunk_capacity)
        payload_cursor = 0
        for index, segment in enumerate(segments):
            offset = len(out)
            if index < changed_from and index < len(self.chunks):
                original = self.chunks[index]
                original_raw = self.payload[payload_cursor:payload_cursor + original.uncompressed_size]
                if segment != original_raw or len(segment) != original.uncompressed_size:
                    raise SaveFormatError("Unexpected data changed before the edited character record")
                block = self.file_bytes[original.offset:original.offset + original.compressed_size]
            else:
                compressed = self._compress_literal_block(segment)
                block = CHUNK_MAGIC + struct.pack("<I", len(segment)) + compressed
            out.extend(block)
            entries.append(Chunk(offset, len(block), len(segment)))
            payload_cursor += len(segment)
        table_offset = len(out)
        out.extend(table)
        out.extend(struct.pack("<I", table_offset))
        out.extend(FOOTER_MAGIC)
        for index, chunk in enumerate(entries):
            struct.pack_into("<III", out, directory_start + index * 12,
                             chunk.offset, chunk.compressed_size, chunk.uncompressed_size)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(out)

    def save_fields_copy(self, output_path: Path, fields: dict[str, int]) -> None:
        """Write selected Int32 payload fields into a separate, validated file."""
        if not fields:
            raise SaveFormatError("No changes were supplied")
        for key, value in fields.items():
            if isinstance(value, bool) or not isinstance(value, int) or not -0x80000000 <= value <= 0x7FFFFFFF:
                raise SaveFormatError(f"{key} must fit a signed 32-bit integer")
        locations = {"money": self.money_location()} if "money" in fields else {}
        nodes = self.nodes
        payload = self.payload
        if any(key != "money" for key in fields):
            snapshot = self.character_snapshot()
            locations.update(snapshot.locations)
            missing_points = {key for key in fields if key in {"attributePoints", "perkPoints"}
                              and key not in snapshot.locations}
            if missing_points:
                payload, nodes, new_locations = self._expand_unspent_points(missing_points)
                locations.update(new_locations)
        edits = {}
        for key, value in fields.items():
            if key not in locations:
                raise SaveFormatError(f"Unsupported editable field: {key}")
            edits[locations[key]] = value
        payload = bytearray(payload)
        for offset, value in edits.items():
            if offset < 0 or offset + 4 > len(payload):
                raise SaveFormatError("An editable field points outside the save payload")
            struct.pack_into("<i", payload, offset, value)

        data = self.file_bytes
        if len(payload) != len(self.payload):
            destination = Path(output_path)
            self._write_resized_payload_copy(destination, bytes(payload), nodes)
            rebuilt = SaveContainer(destination)
            if any(struct.unpack_from("<i", rebuilt.payload, offset)[0] != value
                   for offset, value in edits.items()):
                destination.unlink(missing_ok=True)
                raise SaveFormatError("Rebuilt save did not retain every requested value")
            expected = bytearray(payload)
            for offset, value in edits.items():
                struct.pack_into("<i", expected, offset, value)
            if rebuilt.payload != bytes(expected):
                destination.unlink(missing_ok=True)
                raise SaveFormatError("Rebuilt save changed unexpected decompressed data")
            return
        directory_start = HEADER_SIZE + 8
        directory_end = directory_start + len(self.chunks) * 12
        out = bytearray(data[:directory_end])
        entries = []
        original_cursor = directory_end
        payload_cursor = 0
        target_found = False

        for chunk in self.chunks:
            out.extend(data[original_cursor:chunk.offset])
            new_offset = len(out)
            raw_chunk = self.payload[payload_cursor:payload_cursor + chunk.uncompressed_size]
            raw_chunk_after = bytes(payload[payload_cursor:payload_cursor + chunk.uncompressed_size])
            contains_edit = any(payload_cursor <= offset < payload_cursor + chunk.uncompressed_size for offset in edits)
            if contains_edit:
                target_found = True
            if raw_chunk != raw_chunk_after:
                if not contains_edit:
                    raise SaveFormatError("Unexpected payload change outside an edited field's chunk")
                compressed = self._compress_literal_block(raw_chunk_after)
                chunk_bytes = CHUNK_MAGIC + struct.pack("<I", chunk.uncompressed_size) + compressed
            else:
                chunk_bytes = data[chunk.offset:chunk.offset + chunk.compressed_size]
            out.extend(chunk_bytes)
            entries.append(Chunk(new_offset, len(chunk_bytes), chunk.uncompressed_size))
            original_cursor = chunk.offset + chunk.compressed_size
            payload_cursor += chunk.uncompressed_size

        if not target_found:
            raise SaveFormatError("None of the requested fields fell inside a compressed data chunk")

        table_offset = self.node_table_offset
        out.extend(data[original_cursor:table_offset])
        new_table_offset = len(out)
        out.extend(data[table_offset:-8])
        out.extend(struct.pack("<I", new_table_offset))
        out.extend(FOOTER_MAGIC)

        for index, entry in enumerate(entries):
            struct.pack_into("<III", out, directory_start + index * 12,
                             entry.offset, entry.compressed_size, entry.uncompressed_size)

        destination = Path(output_path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(out)

        # Validate the rebuilt container before the caller installs it in an
        # exported copy. This also ensures only the selected payload field changed.
        rebuilt = SaveContainer(destination)
        if any(struct.unpack_from("<i", rebuilt.payload, offset)[0] != value for offset, value in edits.items()):
            destination.unlink(missing_ok=True)
            raise SaveFormatError("Rebuilt save did not retain every requested value")
        expected = bytearray(self.payload)
        for offset, value in edits.items():
            struct.pack_into("<i", expected, offset, value)
        if rebuilt.payload != bytes(expected):
            destination.unlink(missing_ok=True)
            raise SaveFormatError("Rebuilt save changed unexpected decompressed data")

    def save_money_copy(self, output_path: Path, amount: int) -> None:
        """Compatibility wrapper for wallet-only exports."""
        if not 0 <= amount <= 99_999_999:
            raise SaveFormatError("Money must be a whole number from 0 to 99,999,999")
        self.save_fields_copy(output_path, {"money": amount})
