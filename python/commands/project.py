"""
Project-related command implementations for KiCAD interface
"""

import os
import zipfile
from datetime import datetime
import pcbnew  # type: ignore
import logging
from typing import Dict, Any, Optional

logger = logging.getLogger('kicad_interface')

class ProjectCommands:
    """Handles project-related KiCAD operations"""

    def __init__(self, board: Optional[pcbnew.BOARD] = None):
        """Initialize with optional board instance"""
        self.board = board

    def create_project(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Create a new KiCAD project"""
        try:
            project_name = params.get("projectName", "New_Project")
            path = params.get("path", os.getcwd())
            template = params.get("template")

            # Generate the full project path
            project_path = os.path.join(path, project_name)
            if not project_path.endswith(".kicad_pro"):
                project_path += ".kicad_pro"

            # Create project directory if it doesn't exist
            os.makedirs(os.path.dirname(project_path), exist_ok=True)

            # Create a new board
            board = pcbnew.BOARD()
            
            # Set project properties
            board.GetTitleBlock().SetTitle(project_name)
            
            # Set current date with proper parameter
            from datetime import datetime
            current_date = datetime.now().strftime("%Y-%m-%d")
            board.GetTitleBlock().SetDate(current_date)

            # If template is specified, try to load it
            if template:
                template_path = os.path.expanduser(template)
                if os.path.exists(template_path):
                    template_board = pcbnew.LoadBoard(template_path)
                    # Copy settings from template
                    board.SetDesignSettings(template_board.GetDesignSettings())
                    board.SetLayerStack(template_board.GetLayerStack())

            # Save the board
            board_path = project_path.replace(".kicad_pro", ".kicad_pcb")
            board.SetFileName(board_path)
            pcbnew.SaveBoard(board_path, board)

            # Create project file
            with open(project_path, 'w') as f:
                f.write('{\n')
                f.write('  "board": {\n')
                f.write(f'    "filename": "{os.path.basename(board_path)}"\n')
                f.write('  }\n')
                f.write('}\n')

            self.board = board

            return {
                "success": True,
                "message": f"Created project: {project_name}",
                "project": {
                    "name": project_name,
                    "path": project_path,
                    "boardPath": board_path
                }
            }

        except Exception as e:
            logger.error(f"Error creating project: {str(e)}")
            return {
                "success": False,
                "message": "Failed to create project",
                "errorDetails": str(e)
            }

    def open_project(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Open an existing KiCAD project"""
        try:
            filename = params.get("filename")
            if not filename:
                return {
                    "success": False,
                    "message": "No filename provided",
                    "errorDetails": "The filename parameter is required"
                }

            # Expand user path and make absolute
            filename = os.path.abspath(os.path.expanduser(filename))

            # If it's a project file, get the board file
            if filename.endswith(".kicad_pro"):
                board_path = filename.replace(".kicad_pro", ".kicad_pcb")
            else:
                board_path = filename

            # Load the board
            board = pcbnew.LoadBoard(board_path)
            self.board = board

            return {
                "success": True,
                "message": f"Opened project: {os.path.basename(board_path)}",
                "project": {
                    "name": os.path.splitext(os.path.basename(board_path))[0],
                    "path": filename,
                    "boardPath": board_path
                }
            }

        except Exception as e:
            logger.error(f"Error opening project: {str(e)}")
            return {
                "success": False,
                "message": "Failed to open project",
                "errorDetails": str(e)
            }

    def set_project_properties(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Update project title block metadata."""
        try:
            if not self.board:
                return {
                    "success": False,
                    "message": "No board is loaded",
                    "errorDetails": "Load or create a board first"
                }

            title_block = self.board.GetTitleBlock()

            if "title" in params:
                title_block.SetTitle(params["title"])
            if "company" in params:
                title_block.SetCompany(params["company"])
            if "revision" in params:
                title_block.SetRevision(params["revision"])
            if "date" in params:
                title_block.SetDate(params["date"])

            comments = ["comment1", "comment2", "comment3", "comment4"]
            for idx, key in enumerate(comments):
                if key in params:
                    title_block.SetComment(idx, params[key])

            pcbnew.SaveBoard(self.board.GetFileName(), self.board)

            return {
                "success": True,
                "message": "Updated project properties",
                "project": {
                    "title": title_block.GetTitle(),
                    "company": title_block.GetCompany(),
                    "revision": title_block.GetRevision(),
                    "date": title_block.GetDate(),
                    "comments": [title_block.GetComment(i) for i in range(4)]
                }
            }

        except Exception as e:
            logger.error(f"Error setting project properties: {str(e)}")
            return {
                "success": False,
                "message": "Failed to set project properties",
                "errorDetails": str(e)
            }

    def create_backup(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Create a compressed backup of the current project."""
        try:
            if not self.board:
                return {
                    "success": False,
                    "message": "No board is loaded",
                    "errorDetails": "Load or create a board first"
                }

            board_path = os.path.abspath(self.board.GetFileName())
            project_name = os.path.splitext(os.path.basename(board_path))[0]
            project_file = board_path.replace('.kicad_pcb', '.kicad_pro')
            source_files = [f for f in [board_path, project_file] if os.path.exists(f)]

            backup_dir = params.get("backupPath")
            if backup_dir:
                backup_dir = os.path.abspath(os.path.expanduser(backup_dir))
            else:
                backup_dir = os.path.dirname(board_path)

            os.makedirs(backup_dir, exist_ok=True)
            timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            archive_name = f"{project_name}-backup-{timestamp}.zip"
            archive_path = os.path.join(backup_dir, archive_name)

            with zipfile.ZipFile(archive_path, 'w', zipfile.ZIP_DEFLATED) as archive:
                for file_path in source_files:
                    archive.write(file_path, arcname=os.path.basename(file_path))

            return {
                "success": True,
                "message": "Created project backup",
                "archivePath": archive_path,
                "files": [os.path.basename(f) for f in source_files]
            }

        except Exception as e:
            logger.error(f"Error creating project backup: {str(e)}")
            return {
                "success": False,
                "message": "Failed to create project backup",
                "errorDetails": str(e)
            }

    def archive_project(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Archive the entire project directory into a zip file."""
        try:
            if not self.board:
                return {
                    "success": False,
                    "message": "No board is loaded",
                    "errorDetails": "Load or create a board first"
                }

            output_path = params.get("outputPath")
            if not output_path:
                return {
                    "success": False,
                    "message": "outputPath is required",
                    "errorDetails": "Provide destination archive path"
                }

            output_path = os.path.abspath(os.path.expanduser(output_path))
            os.makedirs(os.path.dirname(output_path), exist_ok=True)

            include_libraries = params.get("includeLibraries", True)
            include_models = params.get("include3dModels", True)

            board_path = os.path.abspath(self.board.GetFileName())
            project_dir = os.path.dirname(board_path)

            def should_include(file_path: str) -> bool:
                lower_path = file_path.lower()
                if not include_libraries and lower_path.endswith(('.lib', '.dcm', '.kicad_sym')):
                    return False
                if not include_models and any(suffix in lower_path for suffix in ('.step', '.stp', '.wrl', '.3dshapes')):
                    return False
                return True

            with zipfile.ZipFile(output_path, 'w', zipfile.ZIP_DEFLATED) as archive:
                for root, _, files in os.walk(project_dir):
                    for file_name in files:
                        full_path = os.path.join(root, file_name)
                        if should_include(full_path):
                            arcname = os.path.relpath(full_path, project_dir)
                            archive.write(full_path, arcname=arcname)

            return {
                "success": True,
                "message": "Archived project",
                "archivePath": output_path
            }

        except Exception as e:
            logger.error(f"Error archiving project: {str(e)}")
            return {
                "success": False,
                "message": "Failed to archive project",
                "errorDetails": str(e)
            }

    def import_project(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Import external project formats (placeholder)."""
        logger.warning("import_project command is not implemented")
        return {
            "success": False,
            "message": "import_project is not implemented yet",
            "errorDetails": "Conversion from external CAD formats requires additional tooling"
        }

    def save_project(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Save the current KiCAD project"""
        try:
            if not self.board:
                return {
                    "success": False,
                    "message": "No board is loaded",
                    "errorDetails": "Load or create a board first"
                }

            filename = params.get("filename")
            if filename:
                # Save to new location
                filename = os.path.abspath(os.path.expanduser(filename))
                self.board.SetFileName(filename)

            # Save the board
            pcbnew.SaveBoard(self.board.GetFileName(), self.board)

            return {
                "success": True,
                "message": f"Saved project to: {self.board.GetFileName()}",
                "project": {
                    "name": os.path.splitext(os.path.basename(self.board.GetFileName()))[0],
                    "path": self.board.GetFileName()
                }
            }

        except Exception as e:
            logger.error(f"Error saving project: {str(e)}")
            return {
                "success": False,
                "message": "Failed to save project",
                "errorDetails": str(e)
            }

    def get_project_info(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Get information about the current project"""
        try:
            if not self.board:
                return {
                    "success": False,
                    "message": "No board is loaded",
                    "errorDetails": "Load or create a board first"
                }

            title_block = self.board.GetTitleBlock()
            filename = self.board.GetFileName()
            
            return {
                "success": True,
                "project": {
                    "name": os.path.splitext(os.path.basename(filename))[0],
                    "path": filename,
                    "title": title_block.GetTitle(),
                    "date": title_block.GetDate(),
                    "revision": title_block.GetRevision(),
                    "company": title_block.GetCompany(),
                    "comment1": title_block.GetComment(0),
                    "comment2": title_block.GetComment(1),
                    "comment3": title_block.GetComment(2),
                    "comment4": title_block.GetComment(3)
                }
            }

        except Exception as e:
            logger.error(f"Error getting project info: {str(e)}")
            return {
                "success": False,
                "message": "Failed to get project information",
                "errorDetails": str(e)
            }
