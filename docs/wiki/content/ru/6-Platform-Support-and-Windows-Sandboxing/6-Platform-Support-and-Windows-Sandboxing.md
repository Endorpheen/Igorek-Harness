# Поддержка платформ и песочница Windows

<details>
<summary>Релевантные исходные файлы</summary>

Следующие файлы использовались как контекст при генерации этой страницы вики:

- [desktop/native/windows-pipes.cpp](desktop/native/windows-pipes.cpp)
- [desktop/opencode-windows.py](desktop/opencode-windows.py)
- [factory/core/platform_support/windows_sandbox.py](factory/core/platform_support/windows_sandbox.py)

</details>



Эта страница даёт обзор зависящей от ОС архитектуры изоляции в Igorek-Harness. Если среды Linux полагаются на Bubblewrap (`bwrap`), то среды Windows используют надёжную многослойную реализацию песочницы в пользовательском режиме. Эта система объединяет ограниченные токены, объекты заданий Windows, изолированные рабочие столы и нативный механизм DLL-инъекции через Microsoft Detours, чтобы защитить дочерние процессы и хост-раннеры OpenCode.

Полные технические детали — на дочерних страницах:
- [Внутреннее устройство WindowsSandbox](#6.1)
- [Нативный пайп-адаптер и Detours](#6.2)
- [Хост-раннер OpenCode для Windows](#6.3)
- [Безопасные примитивы работы с файлами и процессами](#6.4)

---

## 6.1. Внутреннее устройство WindowsSandbox

Класс `WindowsSandbox` — ключевой оркестратор изоляции процессов на платформах Windows [factory/core/platform_support/windows_sandbox.py:57-59](). Он создаёт жёстко ограниченный контекст безопасности для порождаемых дочерних процессов: убирает опасные привилегии, назначает уникальные capability Security Identifiers (SID), выдаёт гранты по Discretionary Access Control List (DACL), навешивает метки целостности, направляет процессы на изолированную window station/рабочий стол и ограничивает выполнение объектом задания Windows.

Подробнее — [Внутреннее устройство WindowsSandbox](#6.1).

Источники: `factory/core/platform_support/windows_sandbox.py:57-132`

---

## 6.2. Нативный пайп-адаптер и Detours

Поскольку ограниченные токены Windows мешают дочернему процессу создавать именованные пайпы с пользовательскими DACL по умолчанию, Igorek-Harness использует нативный C++-модуль (`windows-pipes.cpp`), собранный с вендоренной копией Microsoft Detours. Модуль экспортирует `IgorekEmbed` [desktop/native/windows-pipes.cpp:60-63]() и перехватывает вызовы `CreateNamedPipeA`/`CreateNamedPipeW` и `CreateProcessA`/`CreateProcessW` [desktop/native/windows-pipes.cpp:6-9](), чтобы именованные пайпы, созданные дочерними процессами, наследовали корректные DACL токена по умолчанию.

Подробнее — [Нативный пайп-адаптер и Detours](#6.2).

Источники: `desktop/native/windows-pipes.cpp:1-120`

---

## 6.3. Хост-раннер OpenCode для Windows

Скрипт `desktop/opencode-windows.py` управляет распаковкой, подготовкой окружения и выполнением нативного сервиса OpenCode для Windows внутри `WindowsSandbox` [desktop/opencode-windows.py:1-142](). Он занимается распаковкой архива [desktop/opencode-windows.py:20-29](), путями в формате коротких имён [desktop/opencode-windows.py:49-58](), отображением переменных окружения (включая `SystemRoot`, `HOME` и конфигурации Git) [desktop/opencode-windows.py:90-102](), поиском TCP-порта через `iphlpapi` [desktop/opencode-windows.py:31-46]() и опросом готовности по HTTP [desktop/opencode-windows.py:130-141]().

```mermaid
title "Архитектура хост-раннера OpenCode для Windows"
graph TD
    subcode ["Пространство сущностей кода"]
    Main ["main() <br/> desktop/opencode-windows.py:60"] --> Extract ["extract() <br/> desktop/opencode-windows.py:20"]
    Main --> Sandbox ["WindowsSandbox <br/> factory/core/platform_support/windows_sandbox.py:57"]
    Main --> Port ["listening_port() <br/> desktop/opencode-windows.py:31"]
    Sandbox --> Adapter ["igorek-pipes.dll <br/> desktop/native/windows-pipes.cpp"]
    end
```
Источники: `desktop/opencode-windows.py:1-142`, `factory/core/platform_support/windows_sandbox.py:57-132`, `desktop/native/windows-pipes.cpp:1-120`

Подробнее — [Хост-раннер OpenCode для Windows](#6.3).

---

## 6.4. Безопасные примитивы работы с файлами и процессами

Чтобы предотвратить обход каталогов, состояния гонки и эскалацию привилегий в файловой системе хоста, Igorek-Harness реализует строгие примитивы работы с файлами и процессами. Среди них атомарные записи, чтение с ограничением по рабочей области, эксклюзивные блокировки, зафиксированные родительские каталоги, сохранение DACL и процедуры выполнения git с чекпоинтами.

```mermaid
title "Изоляция платформы и жизненный цикл процессов"
graph TD
    subcode ["Пространство сущностей кода"]
    Script ["desktop/opencode-windows.py"] --> SandboxInit ["WindowsSandbox.__init__() <br/> factory/core/platform_support/windows_sandbox.py:60"]
    SandboxInit --> Bind ["_bind() <br/> factory/core/platform_support/windows_sandbox.py:100"]
    SandboxInit --> Token ["_token() <br/> factory/core/platform_support/windows_sandbox.py:85"]
    SandboxInit --> Grant ["_grant() <br/> factory/core/platform_support/windows_sandbox.py:87"]
    SandboxInit --> Job ["_job() <br/> factory/core/platform_support/windows_sandbox.py:94"]
    SandboxInit --> Desktop ["_desktop() <br/> factory/core/platform_support/windows_sandbox.py:95"]
    end
```
Источники: `factory/core/platform_support/windows_sandbox.py:60-99`, `desktop/opencode-windows.py:60-126`

Подробнее — [Безопасные примитивы работы с файлами и процессами](#6.4).
