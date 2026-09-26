#!/usr/bin/env python3
"""
Validation des données de langues publiées pour PowerDocs.

Le script part de index.json et contrôle chaque langue déclarée :
- cohérence de l'index (LCID, tag de culture, version, liste des fichiers) ;
- présence, taille et empreinte SHA-256 de chaque fichier déclaré, que PowerDocs
  vérifie après téléchargement ;
- contenu des fichiers : dictionnaire Hunspell (.aff/.dic) lisible dans l'encodage
  déclaré par la directive SET, thésaurus (.dat), règles linguistiques
  (language-rules.json) conformes au schéma attendu par l'application ;
- absence de caractères de remplacement U+FFFD, signe d'un fichier abîmé par une
  mauvaise conversion d'encodage.

Lorsqu'une référence de base est fournie (--base), le script compare aussi le
contenu des langues avec cette référence : il exige une nouvelle version pour
toute langue modifiée et produit un récapitulatif des mots et des règles ajoutés
ou supprimés, pour faciliter la relecture d'une pull request.

L'option --update-index recalcule la liste des fichiers (type, taille, empreinte)
de chaque langue de l'index à partir du contenu de son dossier. Elle ne modifie
jamais la version : elle doit être augmentée à la main.

Les messages sont en anglais : ils sont lus par les contributeurs dans les
journaux de l'intégration continue.

Utilisation :
    python .github/scripts/validate_languages.py [--base <git ref>] [--summary <file>] [--update-index]

Code de retour 1 si au moins une erreur est détectée.
"""

import argparse
import codecs
import hashlib
import html
import json
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

# Racine du dépôt, deux niveaux au-dessus de .github/scripts
ROOT = Path(__file__).resolve().parents[2]
INDEX_NAME = "index.json"
LANGUAGES_FOLDER = "dictionaries"
LCID_TABLE_NAME = "lcid.md"
RULES_FILE_NAME = "language-rules.json"

CULTURE_PATTERN = re.compile(r"^[a-z]{2,3}(-[A-Za-z0-9]{2,8})*$")
VERSION_PATTERN = re.compile(r"^\d+\.\d+(\.\d+)?$")
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
NOTE_NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*\.md$")
CONTROL_CHARS_PATTERN = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
REPLACEMENT_CHAR_BYTES = "�".encode("utf-8")

# GitHub refuse les fichiers de plus de 100 Mo et avertit au-delà de 50 Mo
MAX_FILE_SIZE = 50 * 1024 * 1024
MAX_LANGUAGE_SIZE = 100 * 1024 * 1024

# Types de fichiers d'une langue, dans l'ordre où ils sont listés dans l'index
FILE_TYPES = ("affix", "dictionary", "thesaurus", "rules", "notes")
REQUIRED_FILE_TYPES = ("affix", "dictionary")

# Schéma de language-rules.json (voir LanguageRuleSet dans Coho.Apps.FlowDocument)
RULES_SECTIONS = {
    "fillerWords": {"text": True, "explanation": False},
    "anglicisms": {"word": True, "suggestions": True, "explanation": False},
    "homophones": {"id": True, "forms": True, "explanation": False},
    "autoCorrections": {"from": True, "to": True},
}
RULES_KEYS = {"locale", "version", "spaceBeforePunctuation", *RULES_SECTIONS}

# Nombre maximal de mots cités dans le récapitulatif d'une pull request
MAX_LISTED_WORDS = 50

_errors: list[str] = []
_warnings: list[str] = []


def error(message: str) -> None:
    _errors.append(message)


def warn(message: str) -> None:
    _warnings.append(message)


# ---------------------------------------------------------------------------
# Accès aux fichiers (copie de travail ou référence git de base)
# ---------------------------------------------------------------------------

def read_worktree(relative_path: str) -> bytes | None:
    path = ROOT / relative_path
    return path.read_bytes() if path.is_file() else None


def read_git(ref: str, relative_path: str) -> bytes | None:
    """Contenu d'un fichier à une référence git donnée, ou None s'il n'y existe pas."""
    result = subprocess.run(
        ["git", "show", f"{ref}:{relative_path}"],
        cwd=ROOT, capture_output=True, check=False)
    return result.stdout if result.returncode == 0 else None


def git_ref_exists(ref: str) -> bool:
    result = subprocess.run(
        ["git", "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"],
        cwd=ROOT, capture_output=True, check=False)
    return result.returncode == 0


def language_folder(lcid: int) -> str:
    return f"{LANGUAGES_FOLDER}/{lcid}"


# ---------------------------------------------------------------------------
# Index
# ---------------------------------------------------------------------------

def parse_version(value: str) -> tuple[int, ...]:
    return tuple(int(part) for part in value.split("."))


def check_text(value, label: str, required: bool = True, max_length: int = 200) -> bool:
    if value is None and not required:
        return True

    if not isinstance(value, str) or not value.strip():
        error(f"{label}: must be a non-empty string")
        return False

    if value != value.strip():
        error(f"{label}: must not start or end with spaces")
    if CONTROL_CHARS_PATTERN.search(value):
        error(f"{label}: must not contain control characters")
    if len(value) > max_length:
        error(f"{label}: must not exceed {max_length} characters")
    return True


def check_https_url(value, label: str) -> None:
    if value is None:
        return

    if not isinstance(value, str):
        error(f"{label}: must be a string")
        return

    parts = urlsplit(value)
    if parts.scheme != "https" or not parts.hostname:
        error(f"{label}: must be an absolute https:// address ({value})")
    elif parts.username or parts.password:
        error(f"{label}: must not contain credentials ({value})")


def is_integer(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def load_index(data: bytes | None, label: str) -> list[dict] | None:
    if data is None:
        error(f"{label}: file not found")
        return None

    try:
        document = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as ex:
        error(f"{label}: invalid JSON ({ex})")
        return None

    if not isinstance(document, dict) or not isinstance(document.get("languages"), list):
        error(f"{label}: must be an object with a 'languages' array")
        return None

    return document["languages"]


def load_known_lcids() -> set[int]:
    """LCID répertoriés dans lcid.md (colonne décimale), pour signaler une valeur inhabituelle."""
    data = read_worktree(LCID_TABLE_NAME)
    if data is None:
        return set()

    known: set[int] = set()
    for line in data.decode("utf-8", errors="replace").splitlines():
        columns = line.split("\t")
        if len(columns) >= 3 and columns[-1].strip().isdigit():
            known.add(int(columns[-1].strip()))
    return known


def expected_file_type(file_name: str, base_name: str) -> str | None:
    """Type d'un fichier d'après son nom, ou None si le nom n'est pas accepté dans un dossier de langue."""
    if file_name == f"{base_name}.aff":
        return "affix"
    if file_name == f"{base_name}.dic":
        return "dictionary"
    if file_name == f"{base_name}.dat":
        return "thesaurus"
    if file_name == RULES_FILE_NAME:
        return "rules"
    if NOTE_NAME_PATTERN.match(file_name):
        return "notes"
    return None


@dataclass
class IndexFile:
    name: str
    type: str
    size: int | None
    sha256: str | None


@dataclass
class IndexEntry:
    lcid: int
    culture: str
    name: str
    version: str
    files: list[IndexFile]

    @property
    def base_name(self) -> str:
        return self.culture.replace("-", "_")

    @property
    def folder(self) -> str:
        return language_folder(self.lcid)

    def path(self, file: IndexFile) -> str:
        return f"{self.folder}/{file.name}"


def validate_files(item: dict, label: str, base_name: str) -> list[IndexFile] | None:
    files = item.get("files")
    if not isinstance(files, list) or not files:
        error(f"{label}: 'files' must be a non-empty array (run the script with --update-index to fill it)")
        return None

    result: list[IndexFile] = []
    seen_names: set[str] = set()
    allowed_keys = {"name", "type", "size", "sha256"}

    for position, file in enumerate(files):
        file_label = f"{label} > files[{position}]"
        if not isinstance(file, dict):
            error(f"{file_label}: must be an object")
            continue

        for key in file:
            if key not in allowed_keys:
                warn(f"{file_label}: unknown property '{key}' is ignored by the application")

        name = file.get("name")
        if not isinstance(name, str) or not name or "/" in name or "\\" in name or name.startswith("."):
            error(f"{file_label}: 'name' must be a file name, without folder")
            continue

        file_label = f"{label} > {name}"
        if name.lower() in seen_names:
            error(f"{file_label}: declared twice")
            continue
        seen_names.add(name.lower())

        expected_type = expected_file_type(name, base_name)
        if expected_type is None:
            error(f"{file_label}: file name is not accepted; expected {base_name}.aff, {base_name}.dic, "
                  f"{base_name}.dat, {RULES_FILE_NAME} or a .md note")
            continue

        file_type = file.get("type")
        if file_type != expected_type:
            error(f"{file_label}: 'type' must be '{expected_type}'")

        size = file.get("size")
        if not is_integer(size) or size < 0:
            error(f"{file_label}: 'size' is required and must be a positive integer")
            size = None

        sha256 = file.get("sha256")
        if not isinstance(sha256, str) or not SHA256_PATTERN.match(sha256):
            error(f"{file_label}: 'sha256' is required and must be a lowercase hexadecimal SHA-256 digest")
            sha256 = None

        result.append(IndexFile(name, expected_type, size, sha256))

    declared_types = {file.type for file in result}
    for required_type in REQUIRED_FILE_TYPES:
        if required_type not in declared_types:
            error(f"{label}: a '{required_type}' file is required ({base_name}.{'aff' if required_type == 'affix' else 'dic'})")

    return result


def validate_index() -> list[IndexEntry]:
    items = load_index(read_worktree(INDEX_NAME), INDEX_NAME)
    if items is None:
        return []

    known_lcids = load_known_lcids()
    entries: list[IndexEntry] = []
    seen_lcids: set[int] = set()
    seen_cultures: set[str] = set()
    allowed_keys = {"lcid", "culture", "name", "description", "version", "sourceUrl", "license", "files"}

    for position, item in enumerate(items):
        label = f"{INDEX_NAME} > languages[{position}]"
        if not isinstance(item, dict):
            error(f"{label}: must be an object")
            continue

        label = f"{INDEX_NAME} > {item.get('culture', item.get('lcid', f'languages[{position}]'))}"

        for key in item:
            if key not in allowed_keys:
                warn(f"{label}: unknown property '{key}' is ignored by the application")

        lcid = item.get("lcid")
        if not is_integer(lcid) or not 0 < lcid <= 0xFFFF:
            error(f"{label}: 'lcid' must be a Windows language identifier, as a decimal integer (e.g. 1036)")
            continue
        if lcid in seen_lcids:
            error(f"{label}: duplicate lcid {lcid}")
        seen_lcids.add(lcid)
        if known_lcids and lcid not in known_lcids:
            warn(f"{label}: lcid {lcid} is not listed in {LCID_TABLE_NAME}")

        culture = item.get("culture")
        if not isinstance(culture, str) or not CULTURE_PATTERN.match(culture):
            error(f"{label}: 'culture' must be a BCP-47 tag such as 'fr-FR'")
            continue
        if culture.lower() in seen_cultures:
            error(f"{label}: duplicate culture '{culture}'")
        seen_cultures.add(culture.lower())

        check_text(item.get("name"), f"{label} > name", max_length=80)
        check_text(item.get("description"), f"{label} > description", required=False, max_length=300)
        check_text(item.get("license"), f"{label} > license", required=False, max_length=120)
        check_https_url(item.get("sourceUrl"), f"{label} > sourceUrl")

        version = item.get("version")
        if not isinstance(version, str) or not VERSION_PATTERN.match(version):
            error(f"{label}: 'version' is required and must look like '1.0' or '1.0.0'")
            version = None

        if not (ROOT / language_folder(lcid)).is_dir():
            error(f"{label}: folder '{language_folder(lcid)}' not found")
            continue

        files = validate_files(item, label, culture.replace("-", "_"))
        if files is None:
            continue

        if version is not None and isinstance(item.get("name"), str):
            entries.append(IndexEntry(lcid, culture, item["name"], version, files))

    return entries


def report_undeclared_files(entries: list[IndexEntry]) -> None:
    folder = ROOT / LANGUAGES_FOLDER
    if not folder.is_dir():
        return

    declared_folders = {entry.folder: entry for entry in entries}
    for path in sorted(folder.iterdir()):
        relative = f"{LANGUAGES_FOLDER}/{path.name}"
        if not path.is_dir():
            warn(f"{relative}: only language folders are expected in '{LANGUAGES_FOLDER}/'")
            continue

        entry = declared_folders.get(relative)
        if entry is None:
            warn(f"{relative}: folder is not declared in {INDEX_NAME} and will never be offered in PowerDocs")
            continue

        declared = {file.name for file in entry.files}
        for child in sorted(path.iterdir()):
            if child.is_dir():
                error(f"{relative}/{child.name}: sub-folders are not allowed in a language folder")
            elif child.name not in declared:
                warn(f"{relative}/{child.name}: file is not declared in {INDEX_NAME} and will never be downloaded")


# ---------------------------------------------------------------------------
# Contenu des fichiers
# ---------------------------------------------------------------------------

@dataclass
class LanguageContent:
    # Empreinte de chaque fichier déclaré
    digests: dict[str, str] = field(default_factory=dict)
    words: set[str] | None = None
    rules: dict | None = None


def resolve_encoding(name: str, label: str) -> str | None:
    """Convertit un nom d'encodage Hunspell/MyThes (ISO8859-1, microsoft-cp1251...) en codec Python."""
    candidate = name.strip()
    if candidate.lower().startswith("microsoft-"):
        candidate = candidate[len("microsoft-"):]
    try:
        return codecs.lookup(candidate).name
    except LookupError:
        error(f"{label}: unsupported encoding '{name}'")
        return None


def check_replacement_chars(data: bytes, label: str) -> bool:
    count = data.count(REPLACEMENT_CHAR_BYTES)
    if count:
        error(f"{label}: contains {count} U+FFFD replacement character(s); the file was damaged by a wrong encoding conversion")
        return False
    return True


def decode(data: bytes, encoding: str, label: str) -> str | None:
    try:
        text = data.decode(encoding)
    except UnicodeDecodeError as ex:
        error(f"{label}: file cannot be decoded as {encoding} ({ex.reason} at byte {ex.start})")
        return None
    return text[1:] if text.startswith("﻿") else text


def read_affix(data: bytes, label: str, report: bool) -> str | None:
    """Lit un fichier .aff et retourne le codec Python de son encodage (directive SET)."""
    declared = None
    for raw_line in data.splitlines():
        line = raw_line.lstrip(b"\xef\xbb\xbf").strip()
        if line.startswith(b"SET ") or line.startswith(b"SET\t"):
            parts = line.split()
            declared = parts[1].decode("ascii", errors="replace") if len(parts) > 1 else None
            break

    if declared is None:
        if report:
            warn(f"{label}: no SET directive, Hunspell assumes ISO8859-1")
        declared = "ISO8859-1"

    encoding = resolve_encoding(declared, label)
    if encoding is None or not report:
        return encoding

    check_replacement_chars(data, label)
    text = decode(data, encoding, label)
    if text is not None and encoding != "utf-8" and not data.isascii():
        try:
            data.decode("utf-8")
            warn(f"{label}: SET declares {declared} but the file looks UTF-8 encoded")
        except UnicodeDecodeError:
            pass
    return encoding


def read_dictionary(data: bytes, encoding: str | None, label: str, report: bool) -> set[str] | None:
    """Lit un fichier .dic : première ligne = nombre approximatif de mots, puis un mot par ligne."""
    if report:
        check_replacement_chars(data, label)
    text = decode(data, encoding or "utf-8", label) if report else data.decode(encoding or "utf-8", errors="replace")
    if text is None:
        return None

    lines = text.splitlines()
    if not lines or not lines[0].strip().isdigit():
        if report:
            error(f"{label}: first line must be the approximate number of words")
        return None

    # Le mot seul, sans ses drapeaux d'affixes (mot/drapeaux) ni ses données morphologiques
    words = set()
    for line in lines[1:]:
        word = re.split(r"[\s/]", line.strip(), maxsplit=1)[0] if line.strip() else ""
        if word:
            words.add(word)

    if report and not words:
        error(f"{label}: dictionary does not contain any word")
    return words


def read_thesaurus(data: bytes, label: str) -> None:
    """Contrôle un thésaurus MyThes (.dat) : première ligne = encodage, puis les entrées."""
    first_line = data.split(b"\n", 1)[0].lstrip(b"\xef\xbb\xbf").strip().decode("ascii", errors="replace")
    if not first_line:
        error(f"{label}: first line must declare the encoding (e.g. UTF-8)")
        return

    encoding = resolve_encoding(first_line, label)
    check_replacement_chars(data, label)
    if encoding is not None:
        decode(data, encoding, label)


def read_rules(data: bytes, culture: str, label: str, report: bool) -> dict | None:
    """Contrôle language-rules.json d'après le schéma de LanguageRuleSet."""
    try:
        rules = json.loads(data.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as ex:
        if report:
            error(f"{label}: invalid JSON ({ex})")
        return None

    if not isinstance(rules, dict):
        if report:
            error(f"{label}: must be a JSON object")
        return None

    if not report:
        return rules

    for key in rules:
        if key not in RULES_KEYS:
            warn(f"{label}: unknown property '{key}' is ignored by the application")

    if not isinstance(rules.get("locale"), str) or rules["locale"].lower() != culture.lower():
        error(f"{label}: 'locale' must be '{culture}'")
    if "version" in rules and not is_integer(rules["version"]):
        error(f"{label}: 'version' must be an integer")

    for section, properties in RULES_SECTIONS.items():
        entries = rules.get(section, [])
        if not isinstance(entries, list):
            error(f"{label} > {section}: must be an array")
            continue

        for position, entry in enumerate(entries):
            entry_label = f"{label} > {section}[{position}]"
            if not isinstance(entry, dict):
                error(f"{entry_label}: must be an object")
                continue

            for key in entry:
                if key not in properties:
                    warn(f"{entry_label}: unknown property '{key}' is ignored by the application")

            for key, required in properties.items():
                value = entry.get(key)
                if value is None:
                    if required:
                        error(f"{entry_label}: '{key}' is required")
                elif isinstance(value, list):
                    if not value or not all(isinstance(v, str) and v.strip() for v in value):
                        error(f"{entry_label}: '{key}' must be a non-empty array of non-empty strings")
                else:
                    check_text(value, f"{entry_label} > {key}", max_length=500)

    punctuation = rules.get("spaceBeforePunctuation", {})
    if not isinstance(punctuation, dict):
        error(f"{label} > spaceBeforePunctuation: must be an object")
    else:
        for key, value in punctuation.items():
            if key not in ("required", "forbidden"):
                warn(f"{label} > spaceBeforePunctuation: unknown property '{key}' is ignored by the application")
            elif not isinstance(value, list) or not all(isinstance(c, str) and len(c) == 1 for c in value):
                error(f"{label} > spaceBeforePunctuation > {key}: must be an array of single characters")

    return rules


def read_language(entry: IndexEntry, reader, report: bool = True) -> LanguageContent:
    """
    Lit les fichiers déclarés d'une langue. Avec report=False (version de base d'une pull request),
    les anomalies ne sont pas signalées : seul le contenu est extrait pour la comparaison.
    """
    content = LanguageContent()
    total_size = 0
    files = {file.type: file for file in entry.files if file.type != "notes"}
    affix_encoding = None

    for file in sorted(entry.files, key=lambda f: FILE_TYPES.index(f.type)):
        path = entry.path(file)
        data = reader(path)
        if data is None:
            if report:
                error(f"{path}: file not found")
            continue

        content.digests[file.name] = hashlib.sha256(data).hexdigest()
        if not report:
            if file.type == "affix":
                affix_encoding = read_affix(data, path, report=False)
            elif file.type == "dictionary":
                content.words = read_dictionary(data, affix_encoding, path, report=False)
            elif file.type == "rules":
                content.rules = read_rules(data, entry.culture, path, report=False)
            continue

        total_size += len(data)
        if len(data) > MAX_FILE_SIZE:
            error(f"{path}: file exceeds {MAX_FILE_SIZE // (1024 * 1024)} MB")
        if file.size is not None and file.size != len(data):
            error(f"{INDEX_NAME} > {entry.culture} > {file.name}: size is {file.size} but the file is {len(data)} bytes (run --update-index)")
        if file.sha256 is not None and file.sha256 != content.digests[file.name]:
            error(f"{INDEX_NAME} > {entry.culture} > {file.name}: sha256 does not match the file (run --update-index)")

        if file.type == "affix":
            affix_encoding = read_affix(data, path, report=True)
        elif file.type == "dictionary":
            content.words = read_dictionary(data, affix_encoding, path, report=True)
        elif file.type == "thesaurus":
            read_thesaurus(data, path)
        elif file.type == "rules":
            content.rules = read_rules(data, entry.culture, path, report=True)
        elif file.type == "notes":
            check_replacement_chars(data, path)
            decode(data, "utf-8", path)

    if report and total_size > MAX_LANGUAGE_SIZE:
        error(f"{entry.folder}: language exceeds {MAX_LANGUAGE_SIZE // (1024 * 1024)} MB")
    if report and "dictionary" in files and "affix" not in files:
        error(f"{entry.folder}: a dictionary requires its affix file")

    return content


# ---------------------------------------------------------------------------
# Mise à jour de l'index
# ---------------------------------------------------------------------------

def update_index() -> bool:
    """Recalcule la liste des fichiers de chaque langue de l'index à partir du contenu de son dossier."""
    data = read_worktree(INDEX_NAME)
    try:
        document = json.loads(data.decode("utf-8")) if data else None
    except (UnicodeDecodeError, json.JSONDecodeError) as ex:
        print(f"error: {INDEX_NAME}: invalid JSON ({ex})")
        return False

    if not isinstance(document, dict) or not isinstance(document.get("languages"), list):
        print(f"error: {INDEX_NAME}: must be an object with a 'languages' array")
        return False

    for item in document["languages"]:
        if not isinstance(item, dict) or not is_integer(item.get("lcid")) or not isinstance(item.get("culture"), str):
            continue

        folder = ROOT / language_folder(item["lcid"])
        if not folder.is_dir():
            continue

        base_name = item["culture"].replace("-", "_")
        files = []
        for path in folder.iterdir():
            file_type = expected_file_type(path.name, base_name) if path.is_file() else None
            if file_type is None:
                continue
            content = path.read_bytes()
            files.append({"name": path.name, "type": file_type, "size": len(content),
                          "sha256": hashlib.sha256(content).hexdigest()})

        files.sort(key=lambda f: (FILE_TYPES.index(f["type"]), f["name"].lower()))
        if files != item.get("files"):
            item["files"] = files
            print(f"{item['culture']}: files updated, remember to increase 'version' if the content changed")

    (ROOT / INDEX_NAME).write_text(json.dumps(document, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return True


# ---------------------------------------------------------------------------
# Comparaison avec la référence de base et récapitulatif
# ---------------------------------------------------------------------------

@dataclass
class LanguageChange:
    entry: IndexEntry
    status: str  # "new", "updated", "unchanged"
    previous_version: str | None = None
    files_added: list[str] = field(default_factory=list)
    files_modified: list[str] = field(default_factory=list)
    files_removed: list[str] = field(default_factory=list)
    words_added: list[str] = field(default_factory=list)
    words_removed: list[str] = field(default_factory=list)
    rules_changes: list[str] = field(default_factory=list)


def describe_rules_changes(previous: dict | None, current: dict | None) -> list[str]:
    previous = previous or {}
    current = current or {}
    changes = []
    for section in RULES_SECTIONS:
        before = {json.dumps(e, sort_keys=True, ensure_ascii=False) for e in previous.get(section, []) or [] if isinstance(e, dict)}
        after = {json.dumps(e, sort_keys=True, ensure_ascii=False) for e in current.get(section, []) or [] if isinstance(e, dict)}
        if before != after:
            changes.append(f"{section}: {len(after - before)} added or modified, {len(before - after)} removed or modified")
    if previous.get("spaceBeforePunctuation") != current.get("spaceBeforePunctuation"):
        changes.append("spaceBeforePunctuation changed")
    return changes


def compare_with_base(base: str, entries: list[IndexEntry], contents: dict[int, LanguageContent]) -> tuple[list[LanguageChange], list[str]]:
    base_index = read_git(base, INDEX_NAME)
    base_items = load_index(base_index, f"{INDEX_NAME} ({base})") if base_index is not None else []
    base_by_lcid = {item.get("lcid"): item for item in base_items or [] if isinstance(item, dict)}

    changes: list[LanguageChange] = []
    for entry in entries:
        content = contents.get(entry.lcid)
        if content is None:
            continue

        base_item = base_by_lcid.get(entry.lcid)
        if base_item is None or not isinstance(base_item.get("files"), list):
            change = LanguageChange(entry, "new", files_added=sorted(content.digests))
            change.words_added = sorted(content.words or [])
            changes.append(change)
            continue

        base_entry = IndexEntry(entry.lcid, str(base_item.get("culture", entry.culture)), entry.name, str(base_item.get("version")),
                                [IndexFile(f["name"], f.get("type", ""), None, None) for f in base_item["files"]
                                 if isinstance(f, dict) and isinstance(f.get("name"), str) and f.get("type") in FILE_TYPES])
        base_content = read_language(base_entry, lambda path: read_git(base, path), report=False)

        if content.digests == base_content.digests:
            changes.append(LanguageChange(entry, "unchanged"))
            continue

        change = LanguageChange(entry, "updated", previous_version=base_entry.version)
        change.files_added = sorted(name for name in content.digests if name not in base_content.digests)
        change.files_removed = sorted(name for name in base_content.digests if name not in content.digests)
        change.files_modified = sorted(name for name in content.digests
                                       if name in base_content.digests and content.digests[name] != base_content.digests[name])
        change.words_added = sorted((content.words or set()) - (base_content.words or set()))
        change.words_removed = sorted((base_content.words or set()) - (content.words or set()))
        change.rules_changes = describe_rules_changes(base_content.rules, content.rules)
        changes.append(change)

        previous_version = change.previous_version
        if isinstance(previous_version, str) and VERSION_PATTERN.match(previous_version) \
                and parse_version(entry.version) <= parse_version(previous_version):
            error(f"{INDEX_NAME} > {entry.culture}: content changed, its version must be increased (currently {entry.version}, was {previous_version})")

    current_lcids = {entry.lcid for entry in entries}
    removed_languages = sorted(str(item.get("name", lcid)) for lcid, item in base_by_lcid.items() if lcid not in current_lcids)
    return changes, removed_languages


def details(title: str, names: list[str], limit: int | None = None) -> list[str]:
    if not names:
        return []
    listed = names if limit is None else names[:limit]
    more = [f"- … and {len(names) - len(listed)} more"] if len(listed) < len(names) else []
    return [f"<details><summary>{title} ({len(names)})</summary>", "",
            *[f"- {html.escape(n)}" for n in listed], *more, "", "</details>", ""]


def write_summary(path: Path, changes: list[LanguageChange], removed_languages: list[str], base: str | None) -> None:
    lines = ["# Language data validation", ""]

    if _errors:
        lines += [f"❌ **{len(_errors)} error(s)**, {len(_warnings)} warning(s).", ""]
        lines += ["<details open><summary>Errors</summary>", "", *[f"- {html.escape(m)}" for m in _errors], "", "</details>", ""]
    else:
        lines += [f"✅ **Validation passed**, {len(_warnings)} warning(s).", ""]

    if _warnings:
        lines += ["<details><summary>Warnings</summary>", "", *[f"- {html.escape(m)}" for m in _warnings], "", "</details>", ""]

    if base is not None:
        lines += [f"## Changes compared with `{base}`", ""]
        changed = [c for c in changes if c.status != "unchanged"]
        if not changed and not removed_languages:
            lines += ["No language content changed.", ""]

        for change in changed:
            entry = change.entry
            if change.status == "new":
                lines += [f"### 🆕 {html.escape(entry.name)} `{entry.culture}` — version {entry.version}", "",
                          f"New language, {len(change.files_added)} file(s), {len(change.words_added)} word(s).", ""]
                lines += details("Files", change.files_added)
                continue

            lines += [f"### ✏️ {html.escape(entry.name)} `{entry.culture}` — version {change.previous_version} → {entry.version}", "",
                      f"{len(change.words_added)} word(s) added, {len(change.words_removed)} removed.", ""]
            lines += details("Files added", change.files_added)
            lines += details("Files modified", change.files_modified)
            lines += details("Files removed", change.files_removed)
            lines += details("Words added", change.words_added, MAX_LISTED_WORDS)
            lines += details("Words removed", change.words_removed, MAX_LISTED_WORDS)
            lines += details("Language rules", change.rules_changes)

        for name in removed_languages:
            lines += [f"### 🗑️ {html.escape(name)} — removed from the index", ""]

    path.write_text("\n".join(lines), encoding="utf-8")


# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(description="Validate PowerDocs language data.")
    parser.add_argument("--base", help="git reference to compare with (e.g. origin/main)")
    parser.add_argument("--summary", help="write a Markdown summary to this file (e.g. $GITHUB_STEP_SUMMARY)")
    parser.add_argument("--update-index", action="store_true",
                        help="recompute the files (type, size, sha256) of each language in index.json before validating")
    args = parser.parse_args()

    if args.update_index and not update_index():
        return 1

    entries = validate_index()
    report_undeclared_files(entries)

    contents: dict[int, LanguageContent] = {}
    for entry in entries:
        contents[entry.lcid] = read_language(entry, read_worktree)

    base = args.base
    if base is not None and not git_ref_exists(base):
        warn(f"base reference '{base}' not found, changes are not compared")
        base = None

    changes, removed_languages = compare_with_base(base, entries, contents) if base else ([], [])

    for message in _warnings:
        print(f"warning: {message}")
    for message in _errors:
        print(f"error: {message}")

    total_words = sum(len(c.words or ()) for c in contents.values())
    print()
    print(f"{len(entries)} language(s), {total_words} dictionary word(s), {len(_errors)} error(s), {len(_warnings)} warning(s).")

    if args.summary:
        write_summary(Path(args.summary), changes, removed_languages, base)

    if _errors:
        print("Language data validation failed.")
        return 1

    print("Language data validation passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
