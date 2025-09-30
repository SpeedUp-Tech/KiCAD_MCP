from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence, cast

from skip import Schematic
from skip.sexp.util import writeTree
from sexpdata import Symbol

import logging
import os
import tempfile
import uuid

logger = logging.getLogger('kicad_interface')


DEFAULT_SCHEMATIC_VERSION = 20230121
DEFAULT_GENERATOR = "KiCAD-MCP-Server"
DEFAULT_PAPER = "A4"
COMMENT_FIELD_COUNT = 4


def _ensure_comment_list(source: Any, fallback: Sequence[str]) -> List[str]:
    """Normalize comment metadata to a four element list of strings."""
    comments = list(fallback[:COMMENT_FIELD_COUNT])
    if len(comments) < COMMENT_FIELD_COUNT:
        comments.extend([''] * (COMMENT_FIELD_COUNT - len(comments)))

    if source is None:
        return comments

    def _apply(idx: int, value: Any) -> None:
        if value is None:
            return
        comments[idx] = str(value)

    if isinstance(source, (list, tuple)):
        for idx in range(min(len(source), COMMENT_FIELD_COUNT)):
            _apply(idx, source[idx])
        return comments

    if isinstance(source, dict):
        for idx in range(COMMENT_FIELD_COUNT):
            for key in (idx + 1, str(idx + 1), f"comment{idx + 1}"):
                if key in source:
                    _apply(idx, source[key])
                    break
        return comments

    # Unsupported type, fall back to defaults
    return comments


def _build_title_block(title: str, date: str, revision: str, company: str, comments: Sequence[str]) -> list:
    """Create a KiCad title_block S-expression."""
    block: List[Any] = [Symbol('title_block')]
    block.extend([
        [Symbol('title'), title],
        [Symbol('date'), date],
        [Symbol('rev'), revision],
        [Symbol('company'), company],
    ])

    for idx in range(COMMENT_FIELD_COUNT):
        value = comments[idx] if idx < len(comments) else ''
        block.append([
            Symbol('comment'),
            idx + 1,
            value,
        ])

    return block


def _normalise_metadata(name: str, metadata: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Prepare metadata dictionary with sensible defaults."""
    if metadata is None:
        metadata = {}
    elif not isinstance(metadata, dict):
        raise TypeError("metadata must be a mapping if provided")

    today = datetime.now(timezone.utc).strftime('%Y-%m-%d')

    title = str(metadata.get('title') or name)
    date_value = str(metadata.get('date') or today)
    revision = str(metadata.get('revision') or metadata.get('rev') or '')
    company = str(metadata.get('company') or '')
    sheet_name = str(metadata.get('sheet_name') or metadata.get('sheetName') or 'Sheet1')
    description = metadata.get('description') or metadata.get('desc') or ''
    author = metadata.get('author') or ''

    base_comment_defaults = [str(description), str(author)] + [''] * (COMMENT_FIELD_COUNT - 2)
    comments = _ensure_comment_list(metadata.get('comments'), base_comment_defaults)

    for idx in range(COMMENT_FIELD_COUNT):
        key = f'comment{idx + 1}'
        if key in metadata and metadata[key] is not None:
            comments[idx] = str(metadata[key])

    sheet_meta = metadata.get('sheet')
    if not isinstance(sheet_meta, dict):
        sheet_meta = {}
    sheet_title = str(sheet_meta.get('title') or metadata.get('sheet_title') or title)
    sheet_date = str(sheet_meta.get('date') or metadata.get('sheet_date') or date_value)
    sheet_revision = str(sheet_meta.get('revision') or metadata.get('sheet_revision') or revision)
    sheet_company = str(sheet_meta.get('company') or metadata.get('sheet_company') or company)

    sheet_comments = _ensure_comment_list(
        sheet_meta.get('comments') or metadata.get('sheet_comments'),
        comments,
    )
    for idx in range(COMMENT_FIELD_COUNT):
        key = f'comment{idx + 1}'
        if key in sheet_meta and sheet_meta[key] is not None:
            sheet_comments[idx] = str(sheet_meta[key])

    version = metadata.get('version') or DEFAULT_SCHEMATIC_VERSION
    try:
        version_value = int(version)
    except (TypeError, ValueError):
        raise ValueError("version metadata must be convertible to an integer") from None

    generator = str(metadata.get('generator') or DEFAULT_GENERATOR)
    paper = str(metadata.get('paper') or DEFAULT_PAPER)

    return {
        'version': version_value,
        'generator': generator,
        'paper': paper,
        'title': title,
        'date': date_value,
        'revision': revision,
        'company': company,
        'comments': comments,
        'sheet_name': sheet_name,
        'sheet_title': sheet_title,
        'sheet_date': sheet_date,
        'sheet_revision': sheet_revision,
        'sheet_company': sheet_company,
        'sheet_comments': sheet_comments,
    }


def _build_blank_tree(name: str, meta: Dict[str, Any]) -> list:
    """Construct the S-expression tree for an empty schematic compatible with KiCad 9.

    Notes:
    - The root schematic should not contain a hierarchical `(sheet ...)` object when creating
      a brand-new, empty canvas. The page settings and title block live at the root.
    - Include the Root Sheet Instance section `(path "/") (page "1")` as per KiCad's
      schematic file format documentation.
    """
    root_uuid = str(uuid.uuid4())

    logger.debug(
        "Composing schematic tree",
        extra={
            'version': meta['version'],
            'generator': meta['generator'],
            'paper': meta['paper'],
        },
    )

    tree = [
        Symbol('kicad_sch'),
        [Symbol('version'), meta['version']],
        [Symbol('generator'), meta['generator']],
        [Symbol('uuid'), Symbol(root_uuid)],
        [Symbol('paper'), meta['paper']],
        _build_title_block(meta['title'], meta['date'], meta['revision'], meta['company'], meta['comments']),
        # Empty library symbol section (allowed to be empty)
        [Symbol('lib_symbols')],
        # Root Sheet Instance Section (wrap path in sheet_instances)
        [
            Symbol('sheet_instances'),
            [
                Symbol('path'),
                "/",
                [Symbol('page'), "1"],
            ],
        ],
    ]

    return tree


class SchematicManager:
    """Core schematic operations using kicad-skip"""

    @staticmethod
    def create_schematic(name: str, metadata: Optional[Dict[str, Any]] = None) -> Schematic:
        """Create a new empty schematic with optional title metadata."""
        if not isinstance(name, str) or not name.strip():
            raise ValueError("Schematic name must be a non-empty string")

        logger.debug("Creating schematic for '%s'", name)

        meta = _normalise_metadata(name.strip(), metadata)
        tree = _build_blank_tree(name.strip(), meta)

        temp_path: Optional[str] = None
        try:
            with tempfile.NamedTemporaryFile('w', suffix='.kicad_sch', delete=False) as tmp_file:
                temp_path = tmp_file.name

            writeTree(temp_path, tree)

            schematic = Schematic(temp_path)
            cast(Any, schematic).version = str(meta['version'])
            cast(Any, schematic).generator = meta['generator']

            logger.info("Created new schematic '%s' with version %s", name, meta['version'])
            return schematic
        finally:
            if temp_path and os.path.exists(temp_path):
                try:
                    os.remove(temp_path)
                except OSError as exc:
                    logger.warning("Unable to remove temporary schematic template %s: %s", temp_path, exc)

    @staticmethod
    def load_schematic(file_path):
        """Load an existing schematic"""
        if not os.path.exists(file_path):
            logger.error(f"Schematic file not found at {file_path}")
            return None
        try:
            sch = Schematic(file_path)
            logger.info(f"Loaded schematic from: {file_path}")
            return sch
        except Exception as e:
            logger.error(f"Error loading schematic from {file_path}: {e}")
            return None

    @staticmethod
    def save_schematic(schematic, file_path):
        """Save a schematic to file"""
        try:
            # kicad-skip uses write method, not save
            schematic.write(file_path)
            logger.info(f"Saved schematic to: {file_path}")
            return True
        except Exception as e:
            logger.error(f"Error saving schematic to {file_path}: {e}")
            return False

    @staticmethod
    def get_schematic_metadata(schematic):
        """Extract metadata from schematic"""
        # kicad-skip doesn't expose a direct metadata object on Schematic.
        # We can return basic info like version and generator.
        metadata = {
            "version": schematic.version,
            "generator": schematic.generator,
            # Add other relevant properties if needed
        }
        logger.debug("Extracted schematic metadata")
        return metadata

if __name__ == '__main__':
    # Example Usage (for testing)
    # Create a new schematic
    new_sch = SchematicManager.create_schematic("MyTestSchematic")

    # Save the schematic
    test_file = "test_schematic.kicad_sch"
    SchematicManager.save_schematic(new_sch, test_file)

    # Load the schematic
    loaded_sch = SchematicManager.load_schematic(test_file)
    if loaded_sch:
        metadata = SchematicManager.get_schematic_metadata(loaded_sch)
        logger.info(f"Loaded schematic metadata: {metadata}")

    # Clean up test file
    if os.path.exists(test_file):
        os.remove(test_file)
        logger.info(f"Cleaned up {test_file}")
