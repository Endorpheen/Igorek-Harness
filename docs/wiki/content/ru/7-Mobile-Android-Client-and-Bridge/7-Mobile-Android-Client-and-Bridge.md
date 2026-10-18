# Мобильный клиент: Android и мост

<details>
<summary>Используемые исходные файлы</summary>

Для генерации этой страницы вики использовались следующие файлы:

- [android/app/build.gradle.kts](android/app/build.gradle.kts)
- [android/app/src/main/assets/release-notes.json](android/app/src/main/assets/release-notes.json)
- [android/app/src/main/java/ru/igorek/remote/ui/SessionBrowserPanel.kt](android/app/src/main/java/ru/igorek/remote/ui/SessionBrowserPanel.kt)
- [android/build.gradle.kts](android/build.gradle.kts)
- [android/gradle/libs.versions.toml](android/gradle/libs.versions.toml)
- [factory/core/mobile_bridge/service.py](factory/core/mobile_bridge/service.py)

</details>



Эта страница даёт архитектурный обзор экосистемы мобильного удалённого клиента Igorek-Harness, состоящей из нативного Android-приложения (`android/`) и сопутствующей Python-подсистемы `mobile_bridge` (`factory/core/mobile_bridge/`) [factory/core/mobile_bridge/service.py:1-7](). Вместе эти компоненты позволяют пользователю безопасно взаимодействовать с десктопными сессиями агентов, одобрять высокорисковые действия, просматривать удалённые деревья файлов и управлять превью браузера с Android-устройства.

Как родительская страница, этот документ описывает ключевые обязанности клиента и бэкенда моста. Детали реализации см. в разделах [Структура и сборка Android-приложения](#7.1) и [Бэкенд мобильного моста](#7.2).

```mermaid
graph TD
    subgraph "Пространство естественного языка"
        UserDevice["Android-телефон"]
        DesktopHost["Десктопный Python-бэкенд"]
    end

    subgraph "Пространство сущностей кода"
        MainActivity["ru.igorek.remote.MainActivity"]
        BridgeClient["ru.igorek.remote.data.Bridge"]
        MobileBridgeSvc["factory.core.mobile_bridge.service"]
        MobileStore["factory.core.mobile_bridge.store.MobileStore"]
    end

    UserDevice -->|HTTP/REST| MainActivity
    MainActivity --> BridgeClient
    BridgeClient -->|TLS-соединение| MobileBridgeSvc
    MobileBridgeSvc --> MobileStore

    classDef default stroke:#333,stroke-width:2px;
```
*Рисунок 7.1: Сопоставление верхнего уровня между мобильными операциями пользователя и сущностями кода Android-клиента и Python-моста.*

Sources: `android/app/build.gradle.kts:40-50`, `factory/core/mobile_bridge/service.py:1-105`

---

## 7.1. Структура и сборка Android-приложения

Android-клиент — это нативное Jetpack Compose-приложение на Kotlin, ориентированное на современные рантаймы Android (`minSdk 26`, `compileSdk 37`) [android/app/build.gradle.kts:43-46]. Кодовая база организована вокруг модульных UI-панелей (например, `SessionBrowserPanel.kt`), фоновых сервисов моста и интеграции голосовых звонков. Сетевое взаимодействие построено на OkHttp с certificate pinning и собственным обработчиком TLS, а безопасное хранение учётных данных использует EncryptedSharedPreferences.

Система сборки использует Gradle с Kotlin DSL (`build.gradle.kts`), управляя зависимостями AndroidX, компонентами Compose Material 3 и Kotlin-сериализацией через каталоги версий (`gradle/libs.versions.toml`) [android/gradle/libs.versions.toml:1-34]. Release-сборки требуют строгой валидации примечаний к релизу (`verifyReleaseNotes`) и криптографических конфигураций подписи (`verifyReleaseSigning`), что гарантирует безопасные артефакты дистрибутива [android/app/build.gradle.kts:87-127].

Подробности об активити, Compose-панелях, конфигурациях Gradle и подписи релиза см. в разделе [Структура и сборка Android-приложения](#7.1).

Sources: `android/build.gradle.kts:1-6`, `android/app/build.gradle.kts:37-146`, `android/app/src/main/java/ru/igorek/remote/ui/SessionBrowserPanel.kt:72-120`, `android/gradle/libs.versions.toml:1-34`

---

## 7.2. Бэкенд мобильного моста

Бэкенд мобильного моста (`factory/core/mobile_bridge/`) встроен непосредственно в существующий рантайм Igorek, а не работает как отдельный исполнитель [factory/core/mobile_bridge/service.py:1-7]. Он маршрутизирует сообщения через общую очередь `tasks`, дописывает историю в те же журналы `chat_sessions/*.jsonl`, которыми пользуются TUI- и Telegram-клиенты, и разделяет хранилище одобрений и пользовательского ввода [factory/core/mobile_bridge/service.py:1-7].

Ключевые модули бэкенда моста отвечают за безопасные файловые операции (`files.py`), загрузку сессий (`uploads.py`), персистентные хранилища учётных данных и данных сопряжения (`store.py`) и фильтрацию высокорисковых инструментов (`HIGH_RISK_TOOLS`) [factory/core/mobile_bridge/service.py:105-122]. Мост предоставляет конечные точки для проверки сопряжения, инспекции файлов рабочего пространства и процессов удалённого одобрения, сохраняя гарантии изоляции с десктопным ядром.

```mermaid
graph TD
    subgraph "Пространство естественного языка"
        MobileApproval["Центр одобрений"]
        FileInspection["Просмотр файлов рабочего пространства"]
    end

    subgraph "Пространство сущностей кода"
        BridgeService["factory.core.mobile_bridge.service"]
        Store["factory.core.mobile_bridge.store.MobileStore"]
        FileOps["factory.core.mobile_bridge.files"]
        TaskQueue["tasks (SQLite-очередь)"]
    end

    MobileApproval -->|Проверка ожидающих| BridgeService
    BridgeService --> Store
    FileInspection --> FileOps
    BridgeService -->|Постановка действия в очередь| TaskQueue

    classDef default stroke:#333,stroke-width:2px;
```
*Рисунок 7.2: Компоненты бэкенда моста, обрабатывающие мобильные запросы и взаимодействующие с общим хранилищем.*

Sources: `factory/core/mobile_bridge/service.py:1-140`

Подробности о сервере моста, SQLite-хранилищах, маршрутизаторах источников, протоколах безопасного доступа к файлам и покрытии тестами см. в разделе [Бэкенд мобильного моста](#7.2).
