import argparse
import json
import re
import sys
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

try:
	from PIL import Image, ImageTk
except Exception:
	Image = None
	ImageTk = None


SECTION_RE = re.compile(r"^\s*\[([^\]]+)\]\s*$")
THIS_ASSIGN_RE = re.compile(r"^(\s*)this\s*=\s*(.+?)\s*$", re.IGNORECASE)
RESOURCE_SECTION_RE = re.compile(r"^Resource[\w\-\.]+$")
IF_RE = re.compile(r"^\s*if\s+(.+?)\s*$", re.IGNORECASE)
ELSE_IF_RE = re.compile(r"^\s*else\s+if\s+(.+?)\s*$", re.IGNORECASE)
ELSE_RE = re.compile(r"^\s*else\s*$", re.IGNORECASE)
ENDIF_RE = re.compile(r"^\s*endif\s*$", re.IGNORECASE)
RUN_SET_TEXTURES_RE = re.compile(
	r"^\s*run\s*=\s*CommandList\\RabbitFX\\SetTextures\s*$",
	re.IGNORECASE,
)
RUN_RESOURCE_LINE_RE = re.compile(
	r"^\s*Resource\\RabbitFX\\(Diffuse|Lightmap|Normalmap|Materialmap|Cutoutmap|Specialmap)\s*=\s*ref\s+",
	re.IGNORECASE,
)
RUN_RESOURCE_COMMENT_LINE_RE = re.compile(
	r"^\s*;\s*Resource\\RabbitFX\\(Diffuse|Lightmap|Normalmap|Materialmap|Cutoutmap|Specialmap)\s*=\s*ref\s+",
	re.IGNORECASE,
)
RUN_TRIGGER_RE = re.compile(
	r"^\s*run\s*=\s*CommandList\\RabbitFX\\Run\s*$",
	re.IGNORECASE,
)
CHECK_TEXTURE_OVERRIDE_RE = re.compile(r"^\s*CheckTextureOverride\s*=\s*(ps-t\d+)\s*$", re.IGNORECASE)
PS_SLOT_ASSIGN_RE = re.compile(r"^\s*ps-t\d+\s*=\s*\S+", re.IGNORECASE)
DRAW_COMPONENT_COMMENT_RE = re.compile(r"^\s*;\s*Draw Component", re.IGNORECASE)
DRAW_INDEXED_RE = re.compile(r"^\s*drawindexed\s*=", re.IGNORECASE)
RUN_CLEANUP_RE = re.compile(r"^\s*run\s*=\s*CommandListCleanupSharedResources\s*$", re.IGNORECASE)
NONE_OPTION = "<None>"


def split_sections(lines: List[str]) -> List[dict]:
	sections = []
	current_name = None
	current_start = 0

	for i, line in enumerate(lines):
		m = SECTION_RE.match(line)
		if not m:
			continue

		if current_name is not None:
			sections.append({"name": current_name, "start": current_start, "end": i})

		current_name = m.group(1).strip()
		current_start = i

	if current_name is not None:
		sections.append({"name": current_name, "start": current_start, "end": len(lines)})

	return sections


def find_section(sections: List[dict], name: str) -> Optional[dict]:
	for sec in sections:
		if sec["name"].lower() == name.lower():
			return sec
	return None


def build_channel_block(lines: List[str], section: dict, channel_name: str) -> List[str]:
	body = lines[section["start"] + 1 : section["end"]]
	out: List[str] = []

	for raw in body:
		if IF_RE.match(raw) or ELSE_IF_RE.match(raw) or ELSE_RE.match(raw) or ENDIF_RE.match(raw):
			out.append(raw)
			continue

		m = THIS_ASSIGN_RE.match(raw)
		if m:
			indent = m.group(1)
			rhs = m.group(2).strip()
			rhs = re.sub(r"^ref\s+", "", rhs, flags=re.IGNORECASE)
			out.append(f"{indent}Resource\\RabbitFX\\{channel_name} = ref {rhs}\n")

	while out and not out[-1].strip():
		out.pop()

	return out


def is_none_choice(value: Optional[str]) -> bool:
	if value is None:
		return True
	v = value.strip().lower()
	return v in {"", "none", "<none>"}


def normalize_block_for_compare(block: List[str]) -> List[str]:
	rows = [x.rstrip("\r\n") for x in block]
	while rows and not rows[0].strip():
		rows.pop(0)
	while rows and not rows[-1].strip():
		rows.pop()
	return rows


def build_slot_block(lines: List[str], section: dict, slot_name: str) -> List[str]:
	body = lines[section["start"] + 1 : section["end"]]
	out: List[str] = []

	for raw in body:
		if IF_RE.match(raw) or ELSE_IF_RE.match(raw) or ELSE_RE.match(raw) or ENDIF_RE.match(raw):
			out.append(raw)
			continue

		m = THIS_ASSIGN_RE.match(raw)
		if m:
			indent = m.group(1)
			rhs = m.group(2).strip()
			rhs = re.sub(r"^ref\s+", "", rhs, flags=re.IGNORECASE)
			out.append(f"{indent}{slot_name} = {rhs}\n")

	while out and not out[-1].strip():
		out.pop()

	return out


def build_fixed_channel_from_diffuse(diffuse_block: List[str], channel_name: str, fixed_resource: str) -> List[str]:
	out: List[str] = []
	for raw in diffuse_block:
		if THIS_ASSIGN_RE.match(raw):
			continue

		m = RUN_RESOURCE_LINE_RE.match(raw)
		if m:
			indent = raw[: len(raw) - len(raw.lstrip())]
			out.append(f"{indent}Resource\\RabbitFX\\{channel_name} = ref {fixed_resource}\n")
			continue

		this_match = THIS_ASSIGN_RE.match(raw)
		if this_match:
			indent = this_match.group(1)
			out.append(f"{indent}Resource\\RabbitFX\\{channel_name} = ref {fixed_resource}\n")
		else:
			out.append(raw)

	return out


def clean_existing_rabbitfx_block(lines: List[str], start_idx: int, end_idx: int) -> List[str]:
	cleaned = []
	i = start_idx
	while i < end_idx:
		line = lines[i]
		if RUN_TRIGGER_RE.match(line):
			i += 1
			continue
		if RUN_RESOURCE_LINE_RE.match(line):
			i += 1
			continue
		if RUN_SET_TEXTURES_RE.match(line):
			i += 1
			continue
		cleaned.append(line)
		i += 1
	return cleaned


def inject_block_into_component(lines: List[str], section: dict, inject_lines: List[str]) -> List[str]:
	body_start = section["start"] + 1
	body_end = section["end"]
	body = lines[body_start:body_end]

	insert_at = None
	for i, line in enumerate(body):
		if re.match(r"^\s*run\s*=\s*CommandListOverrideSharedResources\s*$", line):
			insert_at = i + 1
			break

	if insert_at is None:
		insert_at = 0

	# Remove stale generated lines that were previously injected before
	# CommandListOverrideSharedResources.
	prefix = body[:insert_at]
	clean_prefix: List[str] = []
	for row in prefix:
		if RUN_TRIGGER_RE.match(row):
			continue
		if RUN_SET_TEXTURES_RE.match(row):
			continue
		if RUN_RESOURCE_LINE_RE.match(row):
			continue
		if RUN_RESOURCE_COMMENT_LINE_RE.match(row):
			continue
		if PS_SLOT_ASSIGN_RE.match(row):
			continue
		clean_prefix.append(row)
	body = clean_prefix + body[insert_at:]
	insert_at = len(clean_prefix)

	# If there's an existing generated mapping block before the first draw call,
	# keep it when already correct, otherwise replace it.
	draw_start = None
	for i in range(insert_at, len(body)):
		if DRAW_COMPONENT_COMMENT_RE.match(body[i]) or DRAW_INDEXED_RE.match(body[i]) or RUN_CLEANUP_RE.match(body[i]):
			draw_start = i
			break
	if draw_start is None:
		draw_start = len(body)

	if draw_start > insert_at:
		candidate = body[insert_at:draw_start]
		has_existing_generated = any(
			RUN_TRIGGER_RE.match(x)
			or RUN_RESOURCE_LINE_RE.match(x)
			or RUN_SET_TEXTURES_RE.match(x)
			or PS_SLOT_ASSIGN_RE.match(x)
			for x in candidate
		)
		if has_existing_generated:
			if normalize_block_for_compare(candidate) == normalize_block_for_compare(inject_lines):
				return lines
			body = body[:insert_at] + body[draw_start:]

	new_body = body[:insert_at] + inject_lines + body[insert_at:]
	return lines[:body_start] + new_body + lines[body_end:]


def list_dds_files(base_dir: Path) -> List[Path]:
	return sorted([p for p in base_dir.rglob("*.dds") if p.is_file()])


def find_resource_sections_with_dds(lines: List[str], sections: List[dict]) -> Dict[str, str]:
	resource_map: Dict[str, str] = {}
	for sec in sections:
		name = sec["name"]
		if not RESOURCE_SECTION_RE.match(name):
			continue
		body = lines[sec["start"] + 1 : sec["end"]]
		for raw in body:
			m = re.match(r"^\s*filename\s*=\s*(.+?)\s*$", raw, re.IGNORECASE)
			if not m:
				continue
			filename = m.group(1).strip()
			if filename.lower().endswith(".dds"):
				resource_map[name] = filename
				break
	return resource_map


def normalize_ini_filename(value: str) -> str:
	return value.replace("\\", "/").strip().lower()


def make_unique_resource_name(base_stem: str, existing: Set[str]) -> str:
	cleaned = re.sub(r"[^A-Za-z0-9_]", "_", base_stem)
	if not cleaned:
		cleaned = "AutoDDS"
	candidate = f"Resource{cleaned}"
	idx = 1
	while candidate.lower() in {x.lower() for x in existing}:
		candidate = f"Resource{cleaned}_{idx}"
		idx += 1
	return candidate


def append_resource_section(ini_text: str, resource_name: str, rel_dds_path: str) -> str:
	snippet = (
		f"\n[{resource_name}]\n"
		f"filename = {rel_dds_path.replace('\\\\', '/')}\n"
	)
	if not ini_text.endswith("\n"):
		ini_text += "\n"
	return ini_text + snippet


def load_image_preview(image_path: Path, max_size: Tuple[int, int] = (360, 360)):
	if Image is None or ImageTk is None:
		raise RuntimeError("Pillow is not available in this build.")
	with Image.open(image_path) as img:
		img = img.convert("RGBA")
		img.thumbnail(max_size)
		return ImageTk.PhotoImage(img.copy())


def open_file_with_default_app(file_path: Path) -> None:
	import os
	import subprocess
	try:
		os.startfile(str(file_path))
	except Exception:
		subprocess.Popen(["cmd", "/c", "start", "", str(file_path)], shell=False)


def _state_file_path() -> Path:
	base_dir = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent
	return base_dir / "rabbitfx_applier_state.json"


def load_ui_state(ini_path: Path) -> Dict[str, object]:
	state_file = _state_file_path()
	if not state_file.exists():
		return {}
	try:
		data = json.loads(state_file.read_text(encoding="utf-8"))
	except Exception:
		return {}
	if not isinstance(data, dict):
		return {}
	item = data.get(str(ini_path.resolve()))
	return item if isinstance(item, dict) else {}


def save_ui_state(ini_path: Path, state: Dict[str, object]) -> None:
	state_file = _state_file_path()
	if state_file.exists():
		try:
			data = json.loads(state_file.read_text(encoding="utf-8"))
		except Exception:
			data = {}
	else:
		data = {}
	if not isinstance(data, dict):
		data = {}
	data[str(ini_path.resolve())] = state
	state_file.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def find_latest_backup_for_ini(ini_path: Path) -> Optional[Path]:
	pattern = f"{ini_path.name}_RabbitFX_*.bak"
	candidates = sorted(ini_path.parent.glob(pattern), key=lambda p: p.stat().st_mtime, reverse=True)
	return candidates[0] if candidates else None


def extract_textureoverride_entries(lines: List[str], section: dict, resource_to_file: Dict[str, str]) -> List[dict]:
	body = lines[section["start"] + 1 : section["end"]]
	condition_stack: List[str] = []
	entries: List[dict] = []

	for raw in body:
		if_match = IF_RE.match(raw)
		if if_match:
			condition_stack.append(if_match.group(1).strip())
			continue

		elseif_match = ELSE_IF_RE.match(raw)
		if elseif_match:
			if condition_stack:
				condition_stack[-1] = elseif_match.group(1).strip()
			continue

		if ELSE_RE.match(raw):
			if condition_stack:
				condition_stack[-1] = "else"
			continue

		if ENDIF_RE.match(raw):
			if condition_stack:
				condition_stack.pop()
			continue

		this_match = THIS_ASSIGN_RE.match(raw)
		if not this_match:
			continue

		rhs = re.sub(r"^ref\s+", "", this_match.group(2).strip(), flags=re.IGNORECASE)
		condition = " && ".join(condition_stack) if condition_stack else "(always)"
		ds_path = resource_to_file.get(rhs, "(no dds filename in ini)")
		entries.append({"condition": condition, "resource": rhs, "dds": ds_path})

	return entries


def build_textureoverride_details(lines: List[str], sections: List[dict], resource_to_file: Dict[str, str]) -> Dict[str, dict]:
	details: Dict[str, dict] = {}
	for sec in sections:
		name = sec["name"]
		if not name.lower().startswith("textureoverridetexture"):
			continue

		raw_lines = lines[sec["start"] : sec["end"]]
		details[name] = {
			"entries": extract_textureoverride_entries(lines, sec, resource_to_file),
			"raw": "".join(raw_lines),
		}

	return details


def extract_ps_slots(lines: List[str], sections: List[dict]) -> List[str]:
	sec = find_section(sections, "CommandListTriggerResourceOverrides")
	if sec is None:
		return []

	slots: List[str] = []
	seen = set()
	for raw in lines[sec["start"] + 1 : sec["end"]]:
		m = CHECK_TEXTURE_OVERRIDE_RE.match(raw)
		if not m:
			continue
		slot = m.group(1).lower()
		if slot not in seen:
			seen.add(slot)
			slots.append(slot)

	return slots


def patch_ini(
	ini_text: str,
	target_components: List[str],
	mode: str,
	include_run_command: bool,
	diffuse_source: str,
	lightmap_source: Optional[str],
	normalmap_source: Optional[str],
	normalmap_resource_override: Optional[str],
	slot_source: Optional[str],
	slot_name: Optional[str],
	include_lightmap: bool,
	include_normalmap: bool,
	materialmap_source: Optional[str],
	include_materialmap: bool,
	cutoutmap_source: Optional[str],
	include_cutoutmap: bool,
	specialmap_source: Optional[str],
	include_specialmap: bool,
) -> str:
	lines = ini_text.splitlines(keepends=True)
	sections = split_sections(lines)

	inject_lines: List[str] = []
	if include_run_command:
		inject_lines.append("run = CommandList\\RabbitFX\\Run\n")

	if mode == "slot":
		if not slot_source:
			raise ValueError("Slot mode requires a source TextureOverride section.")
		if not slot_name:
			raise ValueError("Slot mode requires a slot name (for example ps-t4).")

		slot_sec = find_section(sections, slot_source)
		if slot_sec is None:
			raise ValueError(f"Missing source section: {slot_source}")

		slot_block = build_slot_block(lines, slot_sec, slot_name)
		inject_lines.extend(slot_block)
		if inject_lines and inject_lines[-1].strip():
			inject_lines.append("\n")
	else:
		diffuse_sec = find_section(sections, diffuse_source)
		lightmap_sec = find_section(sections, lightmap_source) if include_lightmap and lightmap_source else None
		normalmap_sec = find_section(sections, normalmap_source) if include_normalmap and normalmap_source else None
		materialmap_sec = find_section(sections, materialmap_source) if include_materialmap and materialmap_source else None
		cutoutmap_sec = find_section(sections, cutoutmap_source) if include_cutoutmap and cutoutmap_source else None
		specialmap_sec = find_section(sections, specialmap_source) if include_specialmap and specialmap_source else None

		missing = [name for name, sec in ((diffuse_source, diffuse_sec),) if sec is None]
		if include_lightmap and lightmap_source and lightmap_sec is None:
			missing.append(lightmap_source)
		if include_normalmap and normalmap_source and not normalmap_resource_override and normalmap_sec is None:
			missing.append(normalmap_source)
		if include_materialmap and materialmap_source and materialmap_sec is None:
			missing.append(materialmap_source)
		if include_cutoutmap and cutoutmap_source and cutoutmap_sec is None:
			missing.append(cutoutmap_source)
		if include_specialmap and specialmap_source and specialmap_sec is None:
			missing.append(specialmap_source)
		if missing:
			raise ValueError("Missing source section(s): " + ", ".join(missing))

		diffuse_block = build_channel_block(lines, diffuse_sec, "Diffuse")
		lightmap_block: List[str] = []
		normalmap_block: List[str] = []
		materialmap_block: List[str] = []
		cutoutmap_block: List[str] = []
		specialmap_block: List[str] = []

		if include_lightmap and lightmap_sec is not None:
			lightmap_block = build_channel_block(lines, lightmap_sec, "Lightmap")

		if include_normalmap:
			if normalmap_resource_override:
				normalmap_block = build_fixed_channel_from_diffuse(
					diffuse_block,
					"Normalmap",
					normalmap_resource_override,
				)
			elif normalmap_sec is not None:
				normalmap_block = build_channel_block(lines, normalmap_sec, "Normalmap")
			else:
				raise ValueError("Provide normalmap source section or normalmap resource override.")

		if include_materialmap and materialmap_sec is not None:
			materialmap_block = build_channel_block(lines, materialmap_sec, "Materialmap")

		if include_cutoutmap and cutoutmap_sec is not None:
			cutoutmap_block = build_channel_block(lines, cutoutmap_sec, "Cutoutmap")

		if include_specialmap and specialmap_sec is not None:
			specialmap_block = build_channel_block(lines, specialmap_sec, "Specialmap")

		inject_lines.extend(diffuse_block)
		if inject_lines and inject_lines[-1].strip():
			inject_lines.append("\n")
		if lightmap_block:
			inject_lines.extend(lightmap_block)
			if inject_lines and inject_lines[-1].strip():
				inject_lines.append("\n")
		if normalmap_block:
			inject_lines.extend(normalmap_block)
			if inject_lines and inject_lines[-1].strip():
				inject_lines.append("\n")
		if materialmap_block:
			inject_lines.extend(materialmap_block)
			if inject_lines and inject_lines[-1].strip():
				inject_lines.append("\n")
		if cutoutmap_block:
			inject_lines.extend(cutoutmap_block)
			if inject_lines and inject_lines[-1].strip():
				inject_lines.append("\n")
		if specialmap_block:
			inject_lines.extend(specialmap_block)
			if inject_lines and inject_lines[-1].strip():
				inject_lines.append("\n")

	inject_lines.append("run = CommandList\\RabbitFX\\SetTextures\n")
	inject_lines.append("\n")

	for comp_name in target_components:
		current_sections = split_sections(lines)
		comp_sec = find_section(current_sections, comp_name)
		if comp_sec is None:
			raise ValueError(f"Missing target component section: {comp_name}")
		lines = inject_block_into_component(lines, comp_sec, inject_lines)

	return "".join(lines)


def run_gui() -> None:
	import tkinter as tk
	from tkinter import filedialog, messagebox, ttk

	root = tk.Tk()
	root.title("RabbitFX INI Applier")
	root.geometry("1700x980")
	root.minsize(1700, 980)
	try:
		root.state("zoomed")
	except Exception:
		pass

	ini_path_var = tk.StringVar()
	status_var = tk.StringVar(value="Pick a mod.ini to begin.")
	normalmap_mode_var = tk.StringVar(value="section")
	output_mode_var = tk.StringVar(value="rabbitfx")
	include_run_var = tk.BooleanVar(value=False)

	components_list: List[str] = []
	texture_sections_list: List[str] = []
	resource_sections_list: List[str] = []
	normalmap_pick_map: Dict[str, Tuple[Optional[str], Optional[str]]] = {}
	texture_details: Dict[str, dict] = {}
	resource_map: Dict[str, str] = {}
	current_preview_image = None
	selected_preview_dds: Optional[str] = None
	ps_slots: List[str] = []

	def collect_current_ui_state() -> Dict[str, object]:
		selected_components: List[str] = []
		for idx in list_components.curselection():
			if 0 <= idx < len(components_list):
				selected_components.append(components_list[idx])
		return {
			"selected_components": selected_components,
			"output_mode": output_mode_var.get(),
			"include_run": bool(include_run_var.get()),
			"normalmap_mode": normalmap_mode_var.get(),
			"diffuse": combo_diffuse.get().strip(),
			"lightmap": combo_lightmap.get().strip(),
			"materialmap": combo_materialmap.get().strip(),
			"cutoutmap": combo_cutoutmap.get().strip(),
			"specialmap": combo_specialmap.get().strip(),
			"slot_source": combo_slot_source.get().strip(),
			"slot_name": combo_ps_slot.get().strip(),
			"normalmap_section": combo_normalmap_section.get().strip(),
			"normalmap_resource": combo_normalmap_resource.get().strip(),
		}

	def browse_ini() -> None:
		selected = filedialog.askopenfilename(
			title="Select mod.ini",
			filetypes=[("INI files", "*.ini"), ("All files", "*.*")],
		)
		if not selected:
			return
		ini_path_var.set(selected)
		load_ini(Path(selected))

	def load_ini(ini_path: Path) -> None:
		nonlocal components_list, texture_sections_list, resource_sections_list, normalmap_pick_map, texture_details, resource_map, ps_slots

		# Capture current UI state so reloading (including after Apply Patch)
		# keeps the user's selections instead of resetting to fixed defaults.
		prev_selected_components: List[str] = []
		if components_list:
			for idx in list_components.curselection():
				if 0 <= idx < len(components_list):
					prev_selected_components.append(components_list[idx])

		prev_diffuse = combo_diffuse.get().strip()
		prev_lightmap = combo_lightmap.get().strip()
		prev_materialmap = combo_materialmap.get().strip()
		prev_cutoutmap = combo_cutoutmap.get().strip()
		prev_specialmap = combo_specialmap.get().strip()
		prev_slot_source = combo_slot_source.get().strip()
		prev_slot = combo_ps_slot.get().strip()
		prev_normalmap_section = combo_normalmap_section.get().strip()
		prev_normalmap_resource = combo_normalmap_resource.get().strip()

		saved = load_ui_state(ini_path)
		if saved:
			prev_selected_components = [str(x) for x in saved.get("selected_components", [])]
			prev_diffuse = str(saved.get("diffuse", prev_diffuse))
			prev_lightmap = str(saved.get("lightmap", prev_lightmap))
			prev_materialmap = str(saved.get("materialmap", prev_materialmap))
			prev_cutoutmap = str(saved.get("cutoutmap", prev_cutoutmap))
			prev_specialmap = str(saved.get("specialmap", prev_specialmap))
			prev_slot_source = str(saved.get("slot_source", prev_slot_source))
			prev_slot = str(saved.get("slot_name", prev_slot))
			prev_normalmap_section = str(saved.get("normalmap_section", prev_normalmap_section))
			prev_normalmap_resource = str(saved.get("normalmap_resource", prev_normalmap_resource))
			if str(saved.get("output_mode", output_mode_var.get())) in {"rabbitfx", "slot"}:
				output_mode_var.set(str(saved.get("output_mode")))
			if str(saved.get("normalmap_mode", normalmap_mode_var.get())) in {"section", "resource"}:
				normalmap_mode_var.set(str(saved.get("normalmap_mode")))
			include_run_var.set(bool(saved.get("include_run", include_run_var.get())))
		try:
			content = ini_path.read_text(encoding="utf-8")
		except UnicodeDecodeError:
			content = ini_path.read_text(encoding="utf-8", errors="replace")

		lines = content.splitlines(keepends=True)
		sections = split_sections(lines)

		components_list = [
			s["name"] for s in sections if s["name"].lower().startswith("textureoverridecomponent")
		]
		texture_sections_list = [
			s["name"] for s in sections if s["name"].lower().startswith("textureoverridetexture")
		]
		resources = find_resource_sections_with_dds(lines, sections)
		resource_map = dict(resources)
		resource_sections_list = sorted(resources.keys())
		texture_details = build_textureoverride_details(lines, sections, resources)
		ps_slots = extract_ps_slots(lines, sections)

		# Build DDS picker list from all files under mod directory.
		dds_paths = list_dds_files(ini_path.parent)
		by_filename: Dict[str, str] = {
			normalize_ini_filename(filename): res_name
			for res_name, filename in resources.items()
		}
		normalmap_pick_map = {}
		pick_values: List[str] = []
		for dds in dds_paths:
			rel = dds.relative_to(ini_path.parent).as_posix()
			normalized_rel = normalize_ini_filename(rel)
			existing_resource = by_filename.get(normalized_rel)
			if existing_resource:
				label = f"{rel} -> {existing_resource}"
				normalmap_pick_map[label] = (existing_resource, rel)
			else:
				label = f"{rel} -> (create new Resource section)"
				normalmap_pick_map[label] = (None, rel)
			pick_values.append(label)

		list_components.delete(0, tk.END)
		for name in components_list:
			list_components.insert(tk.END, name)

		combo_diffuse["values"] = texture_sections_list
		combo_lightmap["values"] = [NONE_OPTION] + texture_sections_list
		combo_normalmap_section["values"] = [NONE_OPTION] + texture_sections_list
		combo_normalmap_resource["values"] = [NONE_OPTION] + pick_values
		combo_materialmap["values"] = [NONE_OPTION] + texture_sections_list
		combo_cutoutmap["values"] = [NONE_OPTION] + texture_sections_list
		combo_specialmap["values"] = [NONE_OPTION] + texture_sections_list
		combo_slot_source["values"] = texture_sections_list
		combo_ps_slot["values"] = ps_slots

		tree_overrides.delete(*tree_overrides.get_children())
		for sec_name in texture_sections_list:
			info = texture_details.get(sec_name, {})
			entries = info.get("entries", [])
			parent = tree_overrides.insert(
				"",
				tk.END,
				text=sec_name,
				values=(f"{len(entries)} entries", "", ""),
			)
			for item in entries:
				tree_overrides.insert(
					parent,
					tk.END,
					text=item["condition"],
					values=(item["resource"], item["dds"], ""),
				)

		def set_texture_combo(combo: ttk.Combobox, previous: str, fallback: str, allow_none: bool) -> None:
			valid_values = [NONE_OPTION] + texture_sections_list if allow_none else texture_sections_list
			if previous in valid_values:
				combo.set(previous)
				return
			if fallback in valid_values:
				combo.set(fallback)
				return
			if allow_none:
				combo.set(NONE_OPTION)
			elif texture_sections_list:
				combo.set(texture_sections_list[0])

		set_texture_combo(combo_diffuse, prev_diffuse, "TextureOverrideTexture16", allow_none=False)
		set_texture_combo(combo_lightmap, prev_lightmap, "TextureOverrideTexture17", allow_none=True)
		set_texture_combo(combo_normalmap_section, prev_normalmap_section, "TextureOverrideTexture18", allow_none=True)
		set_texture_combo(combo_materialmap, prev_materialmap, NONE_OPTION, allow_none=True)
		set_texture_combo(combo_cutoutmap, prev_cutoutmap, NONE_OPTION, allow_none=True)
		set_texture_combo(combo_specialmap, prev_specialmap, NONE_OPTION, allow_none=True)
		set_texture_combo(combo_slot_source, prev_slot_source, "TextureOverrideTexture14", allow_none=False)

		if prev_slot in ps_slots:
			combo_ps_slot.set(prev_slot)
		elif "ps-t4" in ps_slots:
			combo_ps_slot.set("ps-t4")
		elif ps_slots:
			combo_ps_slot.set(ps_slots[0])

		if prev_normalmap_resource in ([NONE_OPTION] + pick_values):
			combo_normalmap_resource.set(prev_normalmap_resource)
		else:
			bt4_pick = ""
			for label, mapped in normalmap_pick_map.items():
				if mapped[0] and mapped[0].lower() == "resourcebt4":
					bt4_pick = label
					break
			if bt4_pick:
				combo_normalmap_resource.set(bt4_pick)
			elif pick_values:
				combo_normalmap_resource.set(pick_values[0])
			else:
				combo_normalmap_resource.set(NONE_OPTION)

		if prev_selected_components:
			for i, name in enumerate(components_list):
				if name in prev_selected_components:
					list_components.selection_set(i)
		if not list_components.curselection() and components_list:
			list_components.selection_set(0)
			list_components.activate(0)
			list_components.see(0)

		status_var.set(
			f"Loaded {ini_path.name} | components: {len(components_list)} | texture sections: {len(texture_sections_list)} | dds files: {len(pick_values)}"
		)
		refresh_preview()

	def get_preview_section_name() -> str:
		if output_mode_var.get() == "slot":
			name = combo_slot_source.get().strip()
			if name:
				return name
		for widget in (combo_diffuse, combo_lightmap, combo_normalmap_section):
			name = widget.get().strip()
			if name:
				return name
		return ""

	def refresh_preview() -> None:
		section_name = get_preview_section_name()
		if not section_name:
			text_structure.delete("1.0", tk.END)
			text_structure.insert(tk.END, "No section selected.")
			text_grouped.delete("1.0", tk.END)
			text_grouped.insert(tk.END, "No grouped preview available.")
			return

		info = texture_details.get(section_name)
		if not info:
			text_structure.delete("1.0", tk.END)
			text_structure.insert(tk.END, f"Section not loaded: {section_name}")
			text_grouped.delete("1.0", tk.END)
			text_grouped.insert(tk.END, "No grouped preview available.")
			return

		text_structure.delete("1.0", tk.END)
		text_structure.insert(tk.END, info.get("raw", ""))

		entries = info.get("entries", [])
		text_grouped.delete("1.0", tk.END)
		if not entries:
			text_grouped.insert(tk.END, "No this = Resource... entries found in this section.")
			return

		for entry in entries:
			text_grouped.insert(
				tk.END,
				f"{entry['condition']}\n  resource: {entry['resource']}\n  dds: {entry['dds']}\n\n",
			)

	def on_tree_select(_event=None) -> None:
		nonlocal selected_preview_dds
		selected = tree_overrides.selection()
		if not selected:
			return
		item_id = selected[0]
		parent = tree_overrides.parent(item_id)
		if not parent:
			section_name = tree_overrides.item(item_id, "text")
			if section_name in texture_sections_list:
				refresh_preview()
				section_children = tree_overrides.get_children(item_id)
				if section_children:
					selected_preview_dds = tree_overrides.set(section_children[0], "dds")
					refresh_image_preview()
			return

		selected_preview_dds = tree_overrides.set(item_id, "dds")
		section_name = tree_overrides.item(parent, "text")
		if section_name in texture_sections_list:
			refresh_preview()
		refresh_image_preview()

	def set_selected_as_diffuse() -> None:
		selected = tree_overrides.selection()
		if not selected:
			messagebox.showinfo("Set Diffuse Source", "Select a TextureOverrideTexture section first.")
			return
		item_id = selected[0]
		parent = tree_overrides.parent(item_id)
		section_name = tree_overrides.item(item_id, "text") if not parent else tree_overrides.item(parent, "text")
		if section_name not in texture_sections_list:
			messagebox.showinfo("Set Diffuse Source", "Select a TextureOverrideTexture section first.")
			return
		combo_diffuse.set(section_name)
		refresh_preview()

	def set_selected_as_lightmap() -> None:
		selected = tree_overrides.selection()
		if not selected:
			messagebox.showinfo("Set Lightmap Source", "Select a TextureOverrideTexture section first.")
			return
		item_id = selected[0]
		parent = tree_overrides.parent(item_id)
		section_name = tree_overrides.item(item_id, "text") if not parent else tree_overrides.item(parent, "text")
		if section_name not in texture_sections_list:
			messagebox.showinfo("Set Lightmap Source", "Select a TextureOverrideTexture section first.")
			return
		combo_lightmap.set(section_name)
		refresh_preview()

	def set_selected_as_normalmap() -> None:
		selected = tree_overrides.selection()
		if not selected:
			messagebox.showinfo("Set Normalmap Source", "Select a TextureOverrideTexture section first.")
			return
		item_id = selected[0]
		parent = tree_overrides.parent(item_id)
		section_name = tree_overrides.item(item_id, "text") if not parent else tree_overrides.item(parent, "text")
		if section_name not in texture_sections_list:
			messagebox.showinfo("Set Normalmap Source", "Select a TextureOverrideTexture section first.")
			return
		combo_normalmap_section.set(section_name)
		normalmap_mode_var.set("section")
		refresh_preview()

	def on_source_change(_event=None) -> None:
		refresh_preview()
		refresh_image_preview()

	def refresh_image_preview() -> None:
		nonlocal current_preview_image, selected_preview_dds
		rel_dds = selected_preview_dds
		if not rel_dds and normalmap_mode_var.get() == "resource":
			selected_label = combo_normalmap_resource.get().strip()
			selected = normalmap_pick_map.get(selected_label)
			if selected:
				_, rel_dds = selected

		if not rel_dds:
			preview_image_label.configure(image="", text="Select a DDS row to preview the texture")
			current_preview_image = None
			return
		image_path = Path(ini_path_var.get().strip()).parent / rel_dds
		if not image_path.exists():
			preview_image_label.configure(image="", text=f"Missing file:\n{rel_dds}")
			current_preview_image = None
			return

		try:
			current_preview_image = load_image_preview(image_path)
			preview_image_label.configure(image=current_preview_image, text=image_path.name)
		except Exception as exc:
			current_preview_image = None
			preview_image_label.configure(
				image="",
				text=(
					f"Preview unavailable for {image_path.name}\n"
					f"{exc}\n\n"
					"This DDS uses a format the built-in preview cannot decode.\n"
					"Double-click the row or use the new Open button to view it externally."
				),
			)

	def open_preview_external() -> None:
		selected = selected_preview_dds
		if not selected:
			messagebox.showinfo("Open DDS", "Select a DDS row first.")
			return
		image_path = Path(ini_path_var.get().strip()).parent / selected
		if not image_path.exists():
			messagebox.showerror("Open DDS", f"File not found:\n{image_path}")
			return
		open_file_with_default_app(image_path)

	def apply_patch_click() -> None:
		from datetime import datetime

		ini_path_text = ini_path_var.get().strip()
		if not ini_path_text:
			messagebox.showerror("Error", "Select a mod.ini first.")
			return

		selected_idx = list_components.curselection()
		if not selected_idx:
			messagebox.showerror("Error", "Select at least one target component.")
			return

		targets = [components_list[i] for i in selected_idx]
		diffuse_source = combo_diffuse.get().strip()
		lightmap_source = combo_lightmap.get().strip()

		normalmap_source = None
		normalmap_resource_override = None
		include_lightmap = not is_none_choice(lightmap_source)
		if not include_lightmap:
			lightmap_source = None

		materialmap_source = combo_materialmap.get().strip()
		include_materialmap = not is_none_choice(materialmap_source)
		if not include_materialmap:
			materialmap_source = None

		cutoutmap_source = combo_cutoutmap.get().strip()
		include_cutoutmap = not is_none_choice(cutoutmap_source)
		if not include_cutoutmap:
			cutoutmap_source = None

		specialmap_source = combo_specialmap.get().strip()
		include_specialmap = not is_none_choice(specialmap_source)
		if not include_specialmap:
			specialmap_source = None

		include_normalmap = True
		if normalmap_mode_var.get() == "section":
			normalmap_source = combo_normalmap_section.get().strip()
			if is_none_choice(normalmap_source):
				include_normalmap = False
				normalmap_source = None
		else:
			chosen_label = combo_normalmap_resource.get().strip()
			if is_none_choice(chosen_label):
				include_normalmap = False
				chosen_label = ""
			elif not chosen_label:
				messagebox.showerror("Error", "Choose a Normalmap resource from DDS list.")
				return
			if chosen_label and chosen_label not in normalmap_pick_map:
				messagebox.showerror("Error", "Invalid DDS selection.")
				return

		ini_path = Path(ini_path_text)
		try:
			original = ini_path.read_text(encoding="utf-8")
		except UnicodeDecodeError:
			original = ini_path.read_text(encoding="utf-8", errors="replace")

		if normalmap_mode_var.get() == "resource" and include_normalmap:
			existing_resource, rel_dds = normalmap_pick_map[chosen_label]
			if existing_resource:
				normalmap_resource_override = existing_resource
			else:
				lines_pre = original.splitlines(keepends=True)
				sections_pre = split_sections(lines_pre)
				existing_names = {s["name"] for s in sections_pre}
				stem = Path(rel_dds).stem if rel_dds else "AutoDDS"
				created_name = make_unique_resource_name(stem, existing_names)
				original = append_resource_section(original, created_name, rel_dds or "")
				normalmap_resource_override = created_name

		try:
			patched = patch_ini(
				original,
				target_components=targets,
				mode=output_mode_var.get(),
				include_run_command=include_run_var.get(),
				diffuse_source=diffuse_source,
				lightmap_source=lightmap_source,
				normalmap_source=normalmap_source,
				normalmap_resource_override=normalmap_resource_override,
				slot_source=combo_slot_source.get().strip() or None,
				slot_name=combo_ps_slot.get().strip() or None,
				include_lightmap=include_lightmap,
				include_normalmap=include_normalmap,
				materialmap_source=materialmap_source,
				include_materialmap=include_materialmap,
				cutoutmap_source=cutoutmap_source,
				include_cutoutmap=include_cutoutmap,
				specialmap_source=specialmap_source,
				include_specialmap=include_specialmap,
			)
		except Exception as exc:
			messagebox.showerror("Patch failed", str(exc))
			return

		timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
		bak_path = ini_path.with_name(f"{ini_path.name}_RabbitFX_{timestamp}.bak")
		bak_path.write_text(original, encoding="utf-8")
		ini_path.write_text(patched, encoding="utf-8")
		save_ui_state(ini_path, collect_current_ui_state())
		load_ini(ini_path)

		messagebox.showinfo(
			"Success",
			f"Patched in place:\n{ini_path}\n\nBackup written:\n{bak_path}",
		)
		status_var.set(f"Patched in place: {ini_path.name} | backup: {bak_path.name}")

	def restore_last_backup_click() -> None:
		ini_path_text = ini_path_var.get().strip()
		if not ini_path_text:
			messagebox.showerror("Restore Backup", "Select a mod.ini first.")
			return

		ini_path = Path(ini_path_text)
		if not ini_path.exists():
			messagebox.showerror("Restore Backup", f"INI file not found:\n{ini_path}")
			return

		latest_backup = find_latest_backup_for_ini(ini_path)
		if latest_backup is None:
			messagebox.showinfo("Restore Backup", "No RabbitFX backups found for this INI yet.")
			return

		confirm = messagebox.askyesno(
			"Restore Backup",
			f"Restore this backup?\n{latest_backup.name}\n\nCurrent INI will be replaced.",
		)
		if not confirm:
			return

		backup_text = latest_backup.read_text(encoding="utf-8", errors="replace")
		ini_path.write_text(backup_text, encoding="utf-8")
		try:
			latest_backup.unlink()
		except Exception:
			pass
		load_ini(ini_path)
		messagebox.showinfo("Restore Backup", f"Restored and removed backup:\n{latest_backup}")
		status_var.set(f"Restored and deleted backup: {latest_backup.name}")

	top = ttk.Frame(root, padding=10)
	top.pack(fill=tk.BOTH, expand=True)

	row_ini = ttk.Frame(top)
	row_ini.pack(fill=tk.X, pady=(0, 10))
	ttk.Label(row_ini, text="mod.ini:").pack(side=tk.LEFT)
	ttk.Entry(row_ini, textvariable=ini_path_var).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=8)
	ttk.Button(row_ini, text="Browse", command=browse_ini).pack(side=tk.LEFT)

	body = ttk.Frame(top)
	body.pack(fill=tk.BOTH, expand=True)

	left = ttk.LabelFrame(body, text="Target Components", padding=8)
	left.pack(side=tk.LEFT, fill=tk.BOTH, expand=False, padx=(0, 6))
	left.configure(width=280)
	left.pack_propagate(False)

	component_list_frame = ttk.Frame(left)
	component_list_frame.pack(fill=tk.BOTH, expand=True)

	list_components = tk.Listbox(
		component_list_frame,
		selectmode=tk.EXTENDED,
		exportselection=False,
		xscrollcommand=lambda *args: x_scroll_components.set(*args),
		yscrollcommand=lambda *args: y_scroll_components.set(*args),
	)
	list_components.grid(row=0, column=0, sticky="nsew")

	x_scroll_components = ttk.Scrollbar(
		component_list_frame,
		orient=tk.HORIZONTAL,
		command=list_components.xview,
	)
	x_scroll_components.grid(row=1, column=0, sticky="ew")

	y_scroll_components = ttk.Scrollbar(
		component_list_frame,
		orient=tk.VERTICAL,
		command=list_components.yview,
	)
	y_scroll_components.grid(row=0, column=1, sticky="ns")

	component_list_frame.grid_rowconfigure(0, weight=1)
	component_list_frame.grid_columnconfigure(0, weight=1)

	right = ttk.LabelFrame(body, text="Source Selection", padding=8)
	right.pack(side=tk.LEFT, fill=tk.Y, expand=False, padx=(6, 6))

	mode_frame_out = ttk.LabelFrame(right, text="Output Mode", padding=8)
	mode_frame_out.pack(fill=tk.X, pady=(0, 8))
	ttk.Radiobutton(mode_frame_out, text="RabbitFX channels", variable=output_mode_var, value="rabbitfx", command=on_source_change).pack(anchor="w")
	ttk.Radiobutton(mode_frame_out, text="PS slot mapping", variable=output_mode_var, value="slot", command=on_source_change).pack(anchor="w")
	ttk.Checkbutton(mode_frame_out, text="Add run = CommandList\\RabbitFX\\Run", variable=include_run_var).pack(anchor="w", pady=(6, 0))

	desc_frame = ttk.LabelFrame(right, text="Texture Type Guide", padding=8)
	desc_frame.pack(fill=tk.X, pady=(0, 8))
	ttk.Label(
		desc_frame,
		text=(
			"Diffuse: Normal texture for the body part (includes color).\n"
			"Lightmaps: Red/Green DDS which matches the Diffuse.\n"
			"Normalmap: Green/Gray DDS which matches the Diffuse.\n"
			"Materialmap: 4th texture used by some characters released after 3.0.\n"
			"Cutoutmap: Extra effect texture used by some characters.\n"
			"Specialmap: Additional effect texture used by some characters (for example Hiyuki).\n"
			"PS-t slot: For face or head regions in some cases."
		),
		justify="left",
	).pack(anchor="w")

	ttk.Label(right, text="Diffuse source section:").pack(anchor="w")
	combo_diffuse = ttk.Combobox(right, state="readonly")
	combo_diffuse.pack(fill=tk.X, pady=(0, 8))

	ttk.Label(right, text="Lightmap source section:").pack(anchor="w")
	combo_lightmap = ttk.Combobox(right, state="readonly")
	combo_lightmap.pack(fill=tk.X, pady=(0, 8))

	ttk.Label(right, text="Materialmap source section:").pack(anchor="w")
	combo_materialmap = ttk.Combobox(right, state="readonly")
	combo_materialmap.pack(fill=tk.X, pady=(0, 8))

	ttk.Label(right, text="Cutoutmap source section:").pack(anchor="w")
	combo_cutoutmap = ttk.Combobox(right, state="readonly")
	combo_cutoutmap.pack(fill=tk.X, pady=(0, 8))

	ttk.Label(right, text="Specialmap source section:").pack(anchor="w")
	combo_specialmap = ttk.Combobox(right, state="readonly")
	combo_specialmap.pack(fill=tk.X, pady=(0, 8))

	ttk.Label(right, text="PS slot source section (slot mode):").pack(anchor="w")
	combo_slot_source = ttk.Combobox(right, state="readonly")
	combo_slot_source.pack(fill=tk.X, pady=(0, 8))

	ttk.Label(right, text="PS slot name (slot mode):").pack(anchor="w")
	combo_ps_slot = ttk.Combobox(right, state="readonly")
	combo_ps_slot.pack(fill=tk.X, pady=(0, 8))

	mode_frame = ttk.LabelFrame(right, text="Normalmap Source", padding=8)
	mode_frame.pack(fill=tk.X, pady=(0, 8))

	ttk.Radiobutton(
		mode_frame,
		text="Copy structure from section",
		variable=normalmap_mode_var,
		value="section",
	).pack(anchor="w")
	combo_normalmap_section = ttk.Combobox(mode_frame, state="readonly")
	combo_normalmap_section.pack(fill=tk.X, pady=(2, 8))

	ttk.Radiobutton(
		mode_frame,
		text="Use one DDS resource for all conditions",
		variable=normalmap_mode_var,
		value="resource",
	).pack(anchor="w")
	combo_normalmap_resource = ttk.Combobox(mode_frame, state="readonly")
	combo_normalmap_resource.pack(fill=tk.X, pady=(2, 0))
	combo_normalmap_resource.bind("<<ComboboxSelected>>", on_source_change)

	inspector = ttk.LabelFrame(body, text="TextureOverride Inspector", padding=8)
	inspector.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 0))

	preview_top = ttk.Frame(inspector)
	preview_top.pack(fill=tk.X, pady=(0, 8))
	preview_image_label = ttk.Label(preview_top, text="DDS preview appears here", anchor="center")
	preview_image_label.pack(fill=tk.BOTH, expand=True)
	preview_image_label.bind("<Double-Button-1>", lambda _event: open_preview_external())

	tree_overrides = ttk.Treeview(inspector, columns=("resource", "dds", "extra"), show="tree headings", height=9)
	tree_overrides.heading("#0", text="TextureOverride / Condition")
	tree_overrides.heading("resource", text="Resource")
	tree_overrides.heading("dds", text="DDS")
	tree_overrides.heading("extra", text="")
	tree_overrides.column("#0", width=360, stretch=True)
	tree_overrides.column("resource", width=170, stretch=False)
	tree_overrides.column("dds", width=260, stretch=True)
	tree_overrides.column("extra", width=1, stretch=False)
	tree_overrides.pack(fill=tk.X, pady=(0, 8))

	inspector_actions = ttk.Frame(inspector)
	inspector_actions.pack(fill=tk.X, pady=(0, 8))
	ttk.Button(inspector_actions, text="Set Selected As Diffuse Source", command=set_selected_as_diffuse).pack(side=tk.LEFT)
	ttk.Button(inspector_actions, text="Set Selected As Lightmap Source", command=set_selected_as_lightmap).pack(side=tk.LEFT, padx=(6, 0))
	ttk.Button(inspector_actions, text="Set Selected As Normalmap Source", command=set_selected_as_normalmap).pack(side=tk.LEFT, padx=(6, 0))

	preview_split = ttk.Panedwindow(inspector, orient=tk.HORIZONTAL)
	preview_split.pack(fill=tk.BOTH, expand=True)

	grouped_frame = ttk.Frame(preview_split)
	structure_frame = ttk.Frame(preview_split)
	preview_split.add(grouped_frame, weight=1)
	preview_split.add(structure_frame, weight=1)

	ttk.Label(grouped_frame, text="Grouped Entries (condition -> resource -> dds)").pack(anchor="w")
	text_grouped = tk.Text(grouped_frame, wrap="word", height=16)
	text_grouped.pack(fill=tk.BOTH, expand=True)

	ttk.Label(structure_frame, text="Raw Section Structure (complex logic preview)").pack(anchor="w")
	text_structure = tk.Text(structure_frame, wrap="none", height=16)
	text_structure.pack(fill=tk.BOTH, expand=True)

	tree_overrides.bind("<<TreeviewSelect>>", on_tree_select)
	combo_diffuse.bind("<<ComboboxSelected>>", on_source_change)
	combo_lightmap.bind("<<ComboboxSelected>>", on_source_change)
	combo_materialmap.bind("<<ComboboxSelected>>", on_source_change)
	combo_cutoutmap.bind("<<ComboboxSelected>>", on_source_change)
	combo_specialmap.bind("<<ComboboxSelected>>", on_source_change)
	combo_slot_source.bind("<<ComboboxSelected>>", on_source_change)
	combo_ps_slot.bind("<<ComboboxSelected>>", on_source_change)
	combo_normalmap_section.bind("<<ComboboxSelected>>", on_source_change)
	combo_normalmap_resource.bind("<<ComboboxSelected>>", on_source_change)

	button_row = ttk.Frame(top)
	button_row.pack(fill=tk.X, pady=(10, 4))
	ttk.Button(button_row, text="Restore Last Backup", command=restore_last_backup_click).pack(side=tk.RIGHT, padx=(6, 0))
	ttk.Button(button_row, text="Open DDS", command=open_preview_external).pack(side=tk.RIGHT, padx=(6, 0))
	ttk.Button(button_row, text="Apply Patch", command=apply_patch_click).pack(side=tk.RIGHT)

	ttk.Label(top, textvariable=status_var).pack(fill=tk.X)

	root.mainloop()


def main() -> None:
	parser = argparse.ArgumentParser(
		description=(
			"Patch mod.ini with RabbitFX blocks by rebuilding conditional structure "
			"from TextureOverride sections."
		)
	)
	parser.add_argument("ini_path", nargs="?", help="Path to mod.ini")
	parser.add_argument("--gui", action="store_true", help="Open GUI mode.")
	parser.add_argument("--mode", choices=["rabbitfx", "slot"], default="rabbitfx", help="Injection mode.")
	parser.add_argument("--include-run-command", dest="include_run_command", action="store_true", default=False, help="Include run = CommandList\\RabbitFX\\Run line.")
	parser.add_argument("--no-run-command", dest="include_run_command", action="store_false", help="Do not insert run = CommandList\\RabbitFX\\Run line.")
	parser.add_argument(
		"--target-component",
		action="append",
		dest="target_components",
		default=None,
		help="Target component section (repeat for multiple). Default: TextureOverrideComponent3",
	)
	parser.add_argument(
		"--diffuse-source",
		default="TextureOverrideTexture16",
		help="Section to copy Diffuse structure from.",
	)
	parser.add_argument(
		"--lightmap-source",
		default="TextureOverrideTexture17",
		help="Section to copy Lightmap structure from.",
	)
	parser.add_argument("--lightmap-none", action="store_true", help="Skip injecting Lightmap lines.")
	parser.add_argument(
		"--normalmap-source",
		default="TextureOverrideTexture18",
		help="Section to copy Normalmap structure from.",
	)
	parser.add_argument("--normalmap-none", action="store_true", help="Skip injecting Normalmap lines.")
	parser.add_argument(
		"--normalmap-resource",
		default="",
		help="Optional resource section (for example ResourceBT4) to force for all Normalmap conditions.",
	)
	parser.add_argument("--materialmap-source", default="", help="Section to copy Materialmap structure from.")
	parser.add_argument("--materialmap-none", action="store_true", help="Skip injecting Materialmap lines.")
	parser.add_argument("--cutoutmap-source", default="", help="Section to copy Cutoutmap structure from.")
	parser.add_argument("--cutoutmap-none", action="store_true", help="Skip injecting Cutoutmap lines.")
	parser.add_argument("--specialmap-source", default="", help="Section to copy Specialmap structure from.")
	parser.add_argument("--specialmap-none", action="store_true", help="Skip injecting Specialmap lines.")
	parser.add_argument("--slot-source", default="", help="Source TextureOverride section for slot mode.")
	parser.add_argument("--slot-name", default="", help="Slot name for slot mode, for example ps-t4.")
	parser.add_argument("--output", default="", help="Optional output path.")
	parser.add_argument("--backup", action="store_true", help="Write .bak when patching in place.")

	args = parser.parse_args()

	if args.gui or not args.ini_path:
		run_gui()
		return

	ini_path = Path(args.ini_path)
	if not ini_path.exists():
		raise FileNotFoundError(f"File not found: {ini_path}")

	try:
		original = ini_path.read_text(encoding="utf-8")
	except UnicodeDecodeError:
		original = ini_path.read_text(encoding="utf-8", errors="replace")

	target_components = args.target_components or ["TextureOverrideComponent3"]
	include_lightmap = not args.lightmap_none and not is_none_choice(args.lightmap_source)
	include_normalmap = not args.normalmap_none and not is_none_choice(args.normalmap_source)
	lightmap_source = None if not include_lightmap else args.lightmap_source
	normalmap_source = None if args.normalmap_resource or not include_normalmap else args.normalmap_source
	normalmap_resource_override = args.normalmap_resource.strip() or None
	include_materialmap = not args.materialmap_none and not is_none_choice(args.materialmap_source)
	materialmap_source = None if not include_materialmap else args.materialmap_source
	include_cutoutmap = not args.cutoutmap_none and not is_none_choice(args.cutoutmap_source)
	cutoutmap_source = None if not include_cutoutmap else args.cutoutmap_source
	include_specialmap = not args.specialmap_none and not is_none_choice(args.specialmap_source)
	specialmap_source = None if not include_specialmap else args.specialmap_source

	patched = patch_ini(
		original,
		target_components=target_components,
		mode=args.mode,
		include_run_command=args.include_run_command,
		diffuse_source=args.diffuse_source,
		lightmap_source=lightmap_source,
		normalmap_source=normalmap_source,
		normalmap_resource_override=normalmap_resource_override,
		slot_source=args.slot_source.strip() or None,
		slot_name=args.slot_name.strip() or None,
		include_lightmap=include_lightmap,
		include_normalmap=include_normalmap,
		materialmap_source=materialmap_source,
		include_materialmap=include_materialmap,
		cutoutmap_source=cutoutmap_source,
		include_cutoutmap=include_cutoutmap,
		specialmap_source=specialmap_source,
		include_specialmap=include_specialmap,
	)

	if args.output:
		out_path = Path(args.output)
		out_path.write_text(patched, encoding="utf-8")
		print(f"Patched file written to: {out_path}")
		return

	if args.backup:
		bak_path = ini_path.with_suffix(ini_path.suffix + ".bak")
		bak_path.write_text(original, encoding="utf-8")
		print(f"Backup created: {bak_path}")

	ini_path.write_text(patched, encoding="utf-8")
	print(f"Patched in place: {ini_path}")


if __name__ == "__main__":
	main()
