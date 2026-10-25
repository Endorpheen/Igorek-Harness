#!/usr/bin/env python3
"""Name: Workspace Cleaner
Description: Module for deleting old tasks and their workspace folders
"""

import os
import shutil
import sqlite3
import argparse
from datetime import datetime, timedelta
from typing import List, Dict, Optional

try:
    from .runtime import get_db_path, get_workspace_root
    from .schema import initialize_database, open_db
except ImportError:  # pragma: no cover - direct script fallback
    from runtime import get_db_path, get_workspace_root
    from schema import initialize_database, open_db

DB_PATH = get_db_path()
WORKSPACE_ROOT = get_workspace_root()
initialize_database(DB_PATH)

class WorkspaceCleaner:
    """Workspace folder and old tasks cleaner"""

    def __init__(self, db_path: str = DB_PATH, workspace_root: str = WORKSPACE_ROOT):
        self.db_path = db_path
        self.workspace_root = workspace_root
        self.deleted_count = 0
        self.freed_space = 0

    def get_task_info(self, task_id: str) -> Optional[Dict]:
        """Retrieves task information"""
        try:
            with open_db(self.db_path) as conn:
                conn.row_factory = sqlite3.Row
                cur = conn.cursor()

                cur.execute("""
                    SELECT task_id, status, created_at, completed_at,
                           input_text, parent_id, profile
                    FROM tasks
                    WHERE task_id = ?
                """, (task_id,))

                row = cur.fetchone()

            if row:
                return dict(row)
            return None

        except Exception as e:
            print(f"[Cleaner] Ошибка получения задачи {task_id}: {e}")
            return None

    def get_workspace_size(self, workspace_path: str) -> int:
        """Retrieves folder size in bytes"""
        total_size = 0
        try:
            for dirpath, dirnames, filenames in os.walk(workspace_path):
                for filename in filenames:
                    filepath = os.path.join(dirpath, filename)
                    try:
                        total_size += os.path.getsize(filepath)
                    except (OSError, IOError):
                        continue
        except Exception:
            pass
        return total_size

    def format_size(self, size_bytes: int) -> str:
        """Formats size into a readable format"""
        for unit in ['B', 'KB', 'MB', 'GB']:
            if size_bytes < 1024.0:
                return f"{size_bytes:.1f} {unit}"
            size_bytes /= 1024.0
        return f"{size_bytes:.1f} TB"

    def delete_task_workspace(self, task_id: str) -> bool:
        """Deletes the task workspace folder"""
        workspace_path = os.path.join(self.workspace_root, task_id)

        if not os.path.exists(workspace_path):
            return False

        try:
            # Get file/folder size before deletion
            size = self.get_workspace_size(workspace_path)

            # Deletes the folder
            shutil.rmtree(workspace_path)

            self.deleted_count += 1
            self.freed_space += size

            return True

        except Exception as e:
            print(f"[Cleaner] Ошибка удаления workspace {task_id}: {e}")
            return False

    def delete_task_from_db(self, task_id: str) -> bool:
        """Deletes the task and all associated database records"""
        try:
            with open_db(self.db_path) as conn:
                cur = conn.cursor()

                # Deletes task events
                cur.execute("DELETE FROM events WHERE task_id = ?", (task_id,))

                cur.execute(
                    "DELETE FROM events WHERE task_id IN "
                    "(SELECT task_id FROM tasks WHERE parent_id = ?)", (task_id,)
                )

                # Deletes subtasks
                cur.execute("DELETE FROM tasks WHERE parent_id = ?", (task_id,))

                # Deletes the task itself
                cur.execute("DELETE FROM tasks WHERE task_id = ?", (task_id,))

                conn.commit()

            return True

        except Exception as e:
            print(f"[Cleaner] Ошибка удаления задачи {task_id} из БД: {e}")
            return False

    def delete_task(self, task_id: str, delete_workspace: bool = True, delete_db: bool = True) -> bool:
        """Completely deletes the task"""
        task_info = self.get_task_info(task_id)
        if not task_info:
            print(f"[Cleaner] Задача {task_id} не найдена")
            return False

        print(f"[Cleaner] Удаление задачи {task_id[:8]}...")
        print(f"  Описание: {task_info['input_text'][:60]}...")
        print(f"  Статус: {task_info['status']}")

        # Deletes the workspace
        if delete_workspace:
            if self.delete_task_workspace(task_id):
                print(f"  ✓ Workspace удален")
            else:
                print(f"  ✗ Workspace не найден или ошибка удаления")

        # Deleting from the database
        if delete_db:
            if self.delete_task_from_db(task_id):
                print(f"  ✓ Задача удалена из БД")
            else:
                print(f"  ✗ Ошибка удаления из БД")
                return False

        return True

    def list_old_tasks(self, days_old: int = 7, status_filter: List[str] = None) -> List[Dict]:
        """Retrieves the list of old tasks"""
        if status_filter is None:
            status_filter = ['ok', 'fail', 'dead_letter']

        cutoff_date = datetime.now() - timedelta(days=days_old)
        cutoff_timestamp = int(cutoff_date.timestamp())

        try:
            with open_db(self.db_path) as conn:
                conn.row_factory = sqlite3.Row
                cur = conn.cursor()

                placeholders = ','.join(['?' for _ in status_filter])
                cur.execute(f"""
                    SELECT task_id, status, created_at, completed_at, input_text, parent_id
                    FROM tasks
                    WHERE status IN ({placeholders})
                      AND created_at < ?
                    ORDER BY created_at DESC
                """, status_filter + [cutoff_timestamp])

                tasks = [dict(row) for row in cur.fetchall()]

            return tasks

        except Exception as e:
            print(f"[Cleaner] Ошибка получения старых задач: {e}")
            return []

    def cleanup_old_tasks(self, days_old: int = 7, dry_run: bool = False,
                         status_filter: List[str] = None) -> None:
        """Clears old tasks"""
        if status_filter is None:
            status_filter = ['ok', 'fail', 'dead_letter']

        print(f"[Cleaner] Поиск задач старше {days_old} дней со статусами: {status_filter}")

        old_tasks = self.list_old_tasks(days_old, status_filter)

        if not old_tasks:
            print("[Cleaner] Старых задач не найдено")
            return

        print(f"[Cleaner] Найдено {len(old_tasks)} старых задач")

        if dry_run:
            print("[Cleaner] Режим предварительного просмотра, ничего не удаляем")
            for task in old_tasks[:10]:  # Displaying the first 10
                workspace_path = os.path.join(self.workspace_root, task['task_id'])
                workspace_exists = os.path.exists(workspace_path)
                size = self.get_workspace_size(workspace_path) if workspace_exists else 0

                created_date = datetime.fromtimestamp(task['created_at'])
                print(f"  {task['task_id'][:8]} | {task['status']} | {created_date.strftime('%Y-%m-%d')} | {self.format_size(size)} | {task['input_text'][:40]}...")

            if len(old_tasks) > 10:
                print(f"  ... и еще {len(old_tasks) - 10} задач")
            return

        # Deleting tasks
        for task in old_tasks:
            self.delete_task(task['task_id'])

        print(f"[Cleaner] Очистка завершена:")
        print(f"  Удалено задач: {self.deleted_count}")
        print(f"  Освобождено места: {self.format_size(self.freed_space)}")

    def cleanup_orphaned_workspaces(self) -> int:
        """Clears workspace folders without corresponding tasks in the database"""
        print("[Cleaner] Поиск orphaned workspace папок...")

        if not os.path.exists(self.workspace_root):
            print("[Cleaner] Папка workspace не найдена")
            return 0

        # Retrieving all task_id from the database
        try:
            with open_db(self.db_path) as conn:
                cur = conn.cursor()
                cur.execute("SELECT task_id FROM tasks")
                db_task_ids = set(row[0] for row in cur.fetchall())
        except Exception as e:
            print(f"[Cleaner] Ошибка получения задач из БД: {e}")
            return 0

        orphaned_count = 0
        orphaned_space = 0

        # Checking all folders in the workspace
        for item in os.listdir(self.workspace_root):
            item_path = os.path.join(self.workspace_root, item)

            if not os.path.isdir(item_path):
                continue

            # If this is a UUID and no such task exists in the database
            if len(item) == 36 and '-' in item:  # UUID format
                if item not in db_task_ids:
                    size = self.get_workspace_size(item_path)
                    try:
                        shutil.rmtree(item_path)
                        orphaned_count += 1
                        orphaned_space += size
                        print(f"  Удалена orphaned папка: {item[:8]}... ({self.format_size(size)})")
                    except Exception as e:
                        print(f"  Ошибка удаления {item}: {e}")

        print(f"[Cleaner] Удалено orphaned папок: {orphaned_count}")
        print(f"[Cleaner] Освобождено места: {self.format_size(orphaned_space)}")

        return orphaned_count

def main():
    """CLI interface"""
    parser = argparse.ArgumentParser(description='Очиститель workspace и старых задач')
    subparsers = parser.add_subparsers(dest='command', help='Команды')

    # Deleting old tasks
    cleanup_parser = subparsers.add_parser('cleanup', help='Очистить старые задачи')
    cleanup_parser.add_argument('--days', type=int, default=7, help='Старше дней (по умолчанию: 7)')
    cleanup_parser.add_argument('--dry-run', action='store_true', help='Предварительный просмотр')
    cleanup_parser.add_argument('--status', nargs='+', default=['ok', 'fail', 'dead_letter'],
                               help='Статусы для удаления (по умолчанию: ok fail dead_letter)')

    # Deleting a specific task
    delete_parser = subparsers.add_parser('delete', help='Удалить конкретную задачу')
    delete_parser.add_argument('task_id', help='ID задачи для удаления')
    delete_parser.add_argument('--keep-db', action='store_true', help='Не удалять из БД')
    delete_parser.add_argument('--keep-workspace', action='store_true', help='Не удалять workspace')

    # Cleaning orphaned folders
    orphaned_parser = subparsers.add_parser('orphaned', help='Очистить orphaned workspace папки')

    # List of old tasks
    list_parser = subparsers.add_parser('list', help='Показать старые задачи')
    list_parser.add_argument('--days', type=int, default=7, help='Старше дней (по умолчанию: 7)')
    list_parser.add_argument('--limit', type=int, default=20, help='Лимит вывода (по умолчанию: 20)')

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return

    cleaner = WorkspaceCleaner()

    if args.command == 'cleanup':
        cleaner.cleanup_old_tasks(
            days_old=args.days,
            dry_run=args.dry_run,
            status_filter=args.status
        )
        # A preview must not mutate orphaned workspaces either.
        if not args.dry_run:
            cleaner.cleanup_orphaned_workspaces()

    elif args.command == 'delete':
        success = cleaner.delete_task(
            args.task_id,
            delete_workspace=not args.keep_workspace,
            delete_db=not args.keep_db
        )
        if not success:
            exit(1)

    elif args.command == 'orphaned':
        cleaner.cleanup_orphaned_workspaces()

    elif args.command == 'list':
        tasks = cleaner.list_old_tasks(args.days)
        print(f"Старые задачи (старше {args.days} дней):")
        print("-" * 100)
        print(f"{'ID':<10} {'Статус':<12} {'Создана':<12} {'Размер':<10} {'Описание'}")
        print("-" * 100)

        for task in tasks[:args.limit]:
            workspace_path = os.path.join(cleaner.workspace_root, task['task_id'])
            size = cleaner.get_workspace_size(workspace_path) if os.path.exists(workspace_path) else 0
            created_date = datetime.fromtimestamp(task['created_at'])
            print(f"{task['task_id'][:8]:<10} {task['status']:<12} {created_date.strftime('%Y-%m-%d'):<12} "
                  f"{cleaner.format_size(size):<10} {task['input_text'][:40]}...")

        if len(tasks) > args.limit:
            print(f"... и еще {len(tasks) - args.limit} задач")

if __name__ == '__main__':
    main()