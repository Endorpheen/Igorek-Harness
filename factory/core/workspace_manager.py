#!/usr/bin/env python3
"""Igorek Agent Factory Workspace Manager
Manages file structure for orchestrated tasks
"""

import os
import json
import shutil
import sqlite3
from datetime import datetime
from pathlib import Path

try:
    from .runtime import get_data_root, get_db_path, get_workspace_root
    from .schema import initialize_database, open_db
    from .workspace_namer import WorkspaceNamer
except ImportError:  # pragma: no cover - direct script fallback
    from runtime import get_data_root, get_db_path, get_workspace_root
    from schema import initialize_database, open_db
    from workspace_namer import WorkspaceNamer

FACTORY_DIR = Path(get_data_root())
DB_PATH = get_db_path()
WORKSPACE_ROOT = get_workspace_root()
initialize_database(DB_PATH)

class WorkspaceManager:
    def __init__(self, db_path: str = DB_PATH, workspace_root: str = WORKSPACE_ROOT):
        self.db_path = db_path
        self.workspace_root = workspace_root
        self.namer = WorkspaceNamer()  # Name generator
        os.makedirs(workspace_root, exist_ok=True)

    def get_workspace_path(self, task_id: str) -> str:
        """Get the path to the task's workspace folder by ID"""
        # Searches for a folder with a human-readable name based on task_id in metadata
        if os.path.exists(self.workspace_root):
            for item in os.listdir(self.workspace_root):
                item_path = os.path.join(self.workspace_root, item)
                if os.path.isdir(item_path):
                    # Verify metadata for confirmation
                    metadata_file = os.path.join(item_path, "metadata.json")
                    if os.path.exists(metadata_file):
                        try:
                            with open(metadata_file, 'r') as f:
                                metadata = json.load(f)
                                if metadata.get('main_task_id') == task_id:
                                    return item_path
                        except:
                            pass

        # If nothing is found, return the default path
        return f"{self.workspace_root}/{task_id}"

    def create_orchestration_workspace(self, main_task_id: str, description: str) -> str:
        """Create a workspace folder for orchestration with the correct structure"""
        # Generates a human-readable name
        readable_name = self.namer.generate_name(description, main_task_id)

        # Creates the main orchestration folder with a human-readable name
        workspace_path = f"{self.workspace_root}/{readable_name}"
        os.makedirs(workspace_path, exist_ok=True)

        # Creating folder structure
        subfolders = [
            "subtasks",           # Subtask results
            "logs",              # Execution logs
            "assets",            # Static assets
            "reports",           # Final reports
            "temp"               # Temporary files
        ]

        for folder in subfolders:
            os.makedirs(f"{workspace_path}/{folder}", exist_ok=True)

        # Creating a README file with task description
        readme_content = f"""# Оркестрация: {main_task_id[:8]}...

## 📋 Описание задачи
{description}

## 📁 Структура папок

- **subtasks/**** - Результаты выполнения подзадач
- **logs/**** - Логи выполнения и отладки
- **assets/** - Статические файлы, изображения, документы
- **reports/** - Финальные отчеты и презентации
- **temp/** - Временные файлы и промежуточные результаты

## 🚀 Информация о выполнении

- **Создано**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
- **Статус**: В процессе выполнения
- **Оркестратор**: Agent Factory v2.0

## 📊 Прогресс

*Прогресс будет отображаться здесь после завершения подзадач*

---

*Файл создан автоматически Agent Factory Orchestrator*
"""

        with open(f"{workspace_path}/README.md", "w", encoding="utf-8") as f:
            f.write(readme_content)

        # Creating an orchestration metadata file
        metadata = {
            "main_task_id": main_task_id,
            "description": description,
            "created_at": datetime.now().isoformat(),
            "workspace_root": workspace_path,
            "subtask_count": 0,
            "completed_subtasks": 0,
            "orchestrator_version": "2.0"
        }

        with open(f"{workspace_path}/metadata.json", "w", encoding="utf-8") as f:
            json.dump(metadata, f, indent=2, ensure_ascii=False)

        print(f"[WorkspaceManager] Создана рабочая папка: {workspace_path}")
        return workspace_path

    def add_subtask_result(self, main_task_id: str, subtask_id: str,
                           profile: str, stage_name: str, result: dict):
        """Add the result of a sub-task execution"""
        workspace_path = self.get_workspace_path(main_task_id)

        # Subtask results folder
        subtask_dir = f"{workspace_path}/subtasks/{profile}_{stage_name}_{subtask_id[:8]}"
        os.makedirs(subtask_dir, exist_ok=True)

        # Saving the result
        result_file = f"{subtask_dir}/result.json"
        with open(result_file, "w", encoding="utf-8") as f:
            json.dump(result, f, indent=2, ensure_ascii=False)

        # If output files exist, copy them
        if result.get("output", {}).get("files"):
            assets_dir = f"{workspace_path}/assets/{profile}_{stage_name}"
            os.makedirs(assets_dir, exist_ok=True)

            for file_info in result["output"]["files"]:
                if os.path.exists(file_info):
                    dest_file = f"{assets_dir}/{os.path.basename(file_info)}"
                    shutil.copy2(file_info, dest_file)
                    print(f"[WorkspaceManager] Скопирован файл: {file_info} -> {dest_file}")

        # Updating metadata
        self.update_metadata(main_task_id)

        print(f"[WorkspaceManager] Добавлен результат подзадачи: {profile}_{stage_name}")
        return subtask_dir

    def generate_final_report(self, main_task_id: str, final_report: str):
        """Generate the final orchestration report"""
        workspace_path = self.get_workspace_path(main_task_id)

        # Generating the final report
        report_content = f"""# 🎯 Финальный отчет оркестрации

**ID задачи**: {main_task_id}
**Дата завершения**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
**Система**: Agent Factory v2.0

---

## 📊 Результаты выполнения

{final_report}

## 📁 Структура артефактов

- `subtasks/` - Результаты выполнения всех подзадач
- `logs/` - Детальные логи выполнения
- `assets/` - Созданные файлы и ресурсы
- `reports/` - Дополнительные отчеты
- `metadata.json` - Метаданные оркестрации

---

*Отчет сгенерирован Agent Factory Orchestrator*
"""

        with open(f"{workspace_path}/reports/final_report.md", "w", encoding="utf-8") as f:
            f.write(report_content)

        # Updating metadata
        metadata = self.get_metadata(main_task_id)
        metadata["completed_at"] = datetime.now().isoformat()
        metadata["status"] = "completed"

        with open(f"{workspace_path}/metadata.json", "w", encoding="utf-8") as f:
            json.dump(metadata, f, indent=2, ensure_ascii=False)

        print(f"[WorkspaceManager] Создан финальный отчет: {workspace_path}/reports/final_report.md")
        return f"{workspace_path}/reports/final_report.md"

    def update_metadata(self, main_task_id: str):
        """Update orchestration metadata"""
        workspace_path = self.get_workspace_path(main_task_id)
        metadata_file = f"{workspace_path}/metadata.json"

        if not os.path.exists(metadata_file):
            return

        try:
            with open(metadata_file, "r", encoding="utf-8") as f:
                metadata = json.load(f)

            # Counting the number of sub-tasks
            with open_db(self.db_path) as conn:
                cur = conn.cursor()
                cur.execute("SELECT COUNT(*) as count FROM tasks WHERE parent_id = ?", (main_task_id,))
                subtask_count = cur.fetchone()[0]

            metadata["subtask_count"] = subtask_count

            # Counting completed sub-tasks
            completed = 0
            if os.path.exists(f"{workspace_path}/subtasks"):
                for item in os.listdir(f"{workspace_path}/subtasks"):
                    if os.path.exists(f"{workspace_path}/subtasks/{item}/result.json"):
                        completed += 1

            metadata["completed_subtasks"] = completed
            metadata["updated_at"] = datetime.now().isoformat()

            with open(metadata_file, "w", encoding="utf-8") as f:
                json.dump(metadata, f, indent=2, ensure_ascii=False)

        except Exception as e:
            print(f"[WorkspaceManager] Ошибка обновления метаданных: {e}")

    def get_metadata(self, main_task_id: str) -> dict:
        """Retrieve orchestration metadata"""
        workspace_path = self.get_workspace_path(main_task_id)
        metadata_file = f"{workspace_path}/metadata.json"

        if os.path.exists(metadata_file):
            with open(metadata_file, "r", encoding="utf-8") as f:
                return json.load(f)

        return {}

    def cleanup_old_workspaces(self, days_old: int = 7):
        """Clear old working folders"""
        import time

        cutoff_time = time.time() - (days_old * 24 * 60 * 60)
        cleaned_count = 0

        try:
            for item in os.listdir(self.workspace_root):
                item_path = os.path.join(self.workspace_root, item)

                if os.path.isdir(item_path):
                    # Check the time of the last modification
                    mtime = os.path.getmtime(item_path)

                    if mtime < cutoff_time:
                        # Verify that this is not an active task
                        task_id = item

                        with open_db(self.db_path) as conn:
                            cur = conn.cursor()
                            cur.execute("SELECT status FROM tasks WHERE task_id = ?", (task_id,))
                            row = cur.fetchone()

                        if not row or row[0] in ['ok', 'fail', 'dead_letter']:
                            print(f"[WorkspaceManager] Удаление старой папки: {item_path}")
                            shutil.rmtree(item_path)
                            cleaned_count += 1

            if cleaned_count > 0:
                print(f"[WorkspaceManager] Очищено {cleaned_count} старых рабочих папок")

        except Exception as e:
            print(f"[WorkspaceManager] Ошибка очистки: {e}")

def main():
    import argparse

    parser = argparse.ArgumentParser(description='Workspace Manager for Agent Factory')
    subparsers = parser.add_subparsers(dest='command', help='Commands')

    # Cleanup old workspaces
    cleanup_parser = subparsers.add_parser('cleanup', help='Clean up old workspaces')
    cleanup_parser.add_argument('--days', type=int, default=7,
                           help='Delete workspaces older than N days (default: 7)')

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return

    workspace_manager = WorkspaceManager()

    if args.command == 'cleanup':
        workspace_manager.cleanup_old_workspaces(args.days)

if __name__ == '__main__':
    main()