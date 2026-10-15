#!/usr/bin/env python3
"""Title: Human-readable name generator for workspace
Description: Converts task descriptions into clear folder names
"""

import re
import html
from contextlib import closing
from datetime import datetime
from typing import Optional

class WorkspaceNamer:
    """Generates human-readable names for workspace folders"""

    def __init__(self, max_length: int = 60):
        self.max_length = max_length

        # Synonyms for replacement
        self.synonyms = {
            'программа': 'прога',
            'приложение': 'приложение',
            'система': 'система',
            'анализ': 'анализ',
            'данные': 'данные',
            'разработка': 'разработка',
            'создай': 'создание',
            'напиши': 'написание',
            'реализуй': 'реализация',
            'простой': 'простая',
            'сложный': 'сложная',
            'базовый': 'базовый',
            'расширенный': 'расширенный'
        }

        # Cleanup templates
        self.clean_patterns = [
            r'[^\w\s\-_]',  # Remove special characters
            r'\s+',          # Repeated spaces
            r'^[_\-]+',      # Leading underscores/hyphens
            r'[_\-]+$',      # Trailing underscores/hyphens
        ]

    def clean_text(self, text: str) -> str:
        """Removes unnecessary characters from text"""
        # HTML entity decoding
        text = html.unescape(text)

        # Convert to lowercase
        text = text.lower()

        # Apply cleaning patterns
        for pattern in self.clean_patterns:
            text = re.sub(pattern, ' ', text)

        # Replace synonyms
        words = text.split()
        words = [self.synonyms.get(word, word) for word in words]

        # Collecting back (restoring or reassembling)
        text = ' '.join(words)

        return text.strip()

    def extract_keywords(self, description: str) -> list:
        """Extracts key terms from the description"""
        # Keywords to preserve (important terms to retain)
        important_words = [
            'калькулятор', 'туду', 'задачи', 'заметки', 'блог', 'чат', 'игра',
            'парсер', 'скрапер', 'бот', 'api', 'веб', 'сайт', 'приложение',
            'база', 'данных', 'анализ', 'статистика', 'отчет', 'график',
            'тест', 'проверка', 'мониторинг', 'лог', 'файл', 'json', 'xml',
            'python', 'javascript', 'html', 'css', 'react', 'vue', 'django',
            'flask', 'fastapi', 'docker', 'kubernetes', 'git', 'linux',
            'авторизация', 'аутентификация', 'регистрация', 'профиль',
            'админ', 'панель', 'интерфейс', 'клиент', 'сервер', 'микросервис',
            'управление', 'программа', 'скрипт', 'утилита', 'сервис', 'портал',
            'магазин', 'корзина', 'товар', 'заказ', 'пользователь', 'комментарий',
            'поиск', 'фильтр', 'сортировка', 'пагинация', 'категория', 'тег'
        ]

        description_lower = description.lower()
        found_words = []

        # Searching for exact matches
        for word in important_words:
            if word in description_lower:
                found_words.append(word)

        # Additional logic for determining task type
        if 'управление' in description_lower and 'задач' in description_lower:
            found_words.append('туду_менеджер')

        if 'json' in description_lower and ('парс' in description_lower or 'обработк' in description_lower or 'статистик' in description_lower):
            found_words.append('json_парсер')

        if 'калькулятор' in description_lower:
            found_words.append('калькулятор')

        if 'лог' in description_lower and ('анализ' in description_lower or 'статистик' in description_lower):
            found_words.append('лог_анализ')

        if 'баз' in description_lower and 'данн' in description_lower:
            found_words.append('база_данных')

        if 'туду' in description_lower or 'todo' in description_lower:
            found_words.append('туду')

        # Removing duplicates and returning
        return list(set(found_words))

    def generate_name(self, description: str, task_id: str = None) -> str:
        """Generates a human-readable folder name"""
        # Clearing the description
        clean_desc = self.clean_text(description)

        # Extract key phrases
        keywords = self.extract_keywords(description)

        # Form the base title
        if keywords:
            # If key phrases exist, use them
            base_name = '_'.join(keywords[:3])  # Maximum of 3 key phrases
        else:
            # Otherwise, use the first words from the description
            words = clean_desc.split()[:4]  # Maximum of 4 words
            base_name = '_'.join(words)

        # If base_name is empty, use the descriptor
        if not base_name:
            base_name = 'задача'

        # Truncate to maximum length
        if len(base_name) > self.max_length:
            base_name = base_name[:self.max_length].rstrip('_')

        # Append date
        date_str = datetime.now().strftime('%m%d_%H%M')

        # Append short ID if provided
        if task_id:
            short_id = task_id[:6]  # Extract first 6 characters of ID
            final_name = f"{base_name}_{date_str}_{short_id}"
        else:
            final_name = f"{base_name}_{date_str}"

        # Final cleanup
        final_name = re.sub(r'[_\-]+', '_', final_name)  # Replaces multiple underscores (_) or hyphens (-) with a single separator.
        final_name = final_name.strip('_')

        return final_name

    def get_task_info(self, task_id: str) -> Optional[dict]:
        """Retrieves task information from the database for name generation"""
        try:
            import sqlite3
            with closing(sqlite3.connect("./database/factory.db")) as conn:
                conn.row_factory = sqlite3.Row
                cur = conn.cursor()

                cur.execute("""
                    SELECT input_text, created_at, status
                    FROM tasks
                    WHERE task_id = ?
                """, (task_id,))

                row = cur.fetchone()

            if row:
                return {
                    'description': row['input_text'],
                    'created_at': row['created_at'],
                    'status': row['status']
                }
            return None

        except Exception as e:
            print(f"[WorkspaceNamer] Ошибка получения задачи {task_id}: {e}")
            return None

def main():
    """CLI interface for testing"""
    import sys
    import argparse

    parser = argparse.ArgumentParser(description='Генератор названий workspace')
    parser.add_argument('description', nargs='?', help='Описание задачи')
    parser.add_argument('--id', help='ID задачи')
    parser.add_argument('--test', action='store_true', help='Тестовый режим')

    args = parser.parse_args()

    namer = WorkspaceNamer()

    if args.test:
        # Test examples
        test_descriptions = [
            "Создай простую программу для управления задачами с сохранением в JSON",
            "Напиши калькулятор на Python с базовыми операциями",
            "Разработай REST API для блога с аутентификацией",
            "Проанализируй логи веб-сервера и найди топ IP адресов",
            "Создай парсер для сайта с вакансиями"
        ]

        print("=== Тест генерации названий ===")
        for desc in test_descriptions:
            name = namer.generate_name(desc)
            print(f"Описание: {desc[:50]}...")
            print(f"Название: {name}")
            print("-" * 50)
    else:
        if not args.description:
            parser.print_help()
            return
        name = namer.generate_name(args.description, args.id)
        print(name)

if __name__ == '__main__':
    main()