#!/usr/bin/env python3
"""Sending tasks to Agent Factory from a Matrix bot
Usage: python3 send_to_factory.py "task text" [profile]
"""

import json
import sys
import os
import requests
import argparse

API_URL = os.environ.get("AGENT_FACTORY_API_URL", "http://localhost:8180").rstrip("/")

def send_task(text, profile="zai"):
    """Send a task to Factory"""
    try:
        data = {
            "text": text,
            "profile": profile,
            "capabilities": [],
            "metadata": {
                "source": "matrix",
                "created_via": "matrix_bot"
            }
        }

        response = requests.post(
            f"{API_URL}/task",
            json=data,
            headers={"Content-Type": "application/json",
                     "X-API-Key": os.environ.get("FACTORY_API_KEY", "")}
        )

        if response.status_code == 201:
            result = response.json()
            task_id = result["task_id"]
            print(f"✅ Задача создана: {task_id}")
            print(f"📋 Профиль: {profile}")
            print(f"🔗 Статус: {API_URL}/task/{task_id}")
            return task_id
        else:
            print(f"❌ Ошибка: {response.status_code}")
            print(response.text)
            return None

    except Exception as e:
        print(f"❌ Ошибка отправки: {e}")
        return None

def get_task_status(task_id):
    """Retrieve task status"""
    try:
        response = requests.get(f"{API_URL}/task/{task_id}",
                                headers={"X-API-Key": os.environ.get("FACTORY_API_KEY", "")})

        if response.status_code == 200:
            data = response.json()
            print(f"\n📊 Статус задачи: {task_id[:8]}...")
            print(f"   Статус: {data['status']}")
            print(f"   Профиль: {data.get('profile', 'N/A')}")
            print(f"   Создана: {data['created_at']}")
            if data.get('result_text'):
                print(f"\n📝 Результат:")
                print(data['result_text'][:500] + "..." if len(data['result_text']) > 500 else data['result_text'])
            if data.get('workspace_files'):
                print(f"\n📁 Файлы: {len(data['workspace_files'])}")
                for f in data['workspace_files'][:5]:
                    print(f"   - {f}")
            return data
        else:
            print(f"❌ Задача не найдена: {task_id}")
            return None

    except Exception as e:
        print(f"❌ Ошибка получения статуса: {e}")
        return None

def main():
    parser = argparse.ArgumentParser(description='Отправка задач в Agent Factory')
    parser.add_argument('text', nargs='?', help='Текст задачи')
    parser.add_argument('--profile', default='zai', help='Профиль (zai/minimax/orchestrator)')
    parser.add_argument('--status', help='Проверить статус задачи')
    args = parser.parse_args()

    if args.status:
        get_task_status(args.status)
    else:
        if args.text is None:
            parser.error("text is required unless --status is used")
        task_id = send_task(args.text, args.profile)
        if task_id:
            print(f"\n⏳ Ожидание выполнения...")
            print(f"   Для проверки статуса: python3 {__file__} --status {task_id}")

if __name__ == '__main__':
    main()
