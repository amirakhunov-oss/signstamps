# SignStamps

Веб-сервис и ML-пайплайн для проверки сканов документов на наличие подписей и печатей.

## Что внутри

- FastAPI-приложение `accountant_tool/`.
- HTML-интерфейс для загрузки PDF, просмотра детекций, ручной проверки и работы с эталонами.
- YOLO-инференс для классов `signature` и `stamp`.
- Локальное сравнение найденных crop-изображений с эталонами через OpenCV.
- Скрипты подготовки CVAT/YOLO-разметки в `scripts/`.
- Документация по развертыванию и отчет о выполненных работах.

## Локальный запуск

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r accountant_tool\requirements.txt
.\.venv\Scripts\python.exe -m uvicorn accountant_tool.app:app --host 127.0.0.1 --port 8011
```

Сервис ожидает YOLO-модель по пути из переменной `SIGNSTAMP_YOLO_MODEL`. Если переменная не задана, используется локальный путь к весам из лабораторного окружения.

```powershell
$env:SIGNSTAMP_YOLO_MODEL = "C:\path\to\best.pt"
```

## Данные и веса

Крупные артефакты не хранятся в git:

- исходные сканы;
- датасеты;
- CVAT-архивы;
- результаты обучения;
- веса моделей;
- локальная SQLite-база;
- runtime storage.

Это сделано намеренно: часть файлов крупнее лимитов GitHub, а сканы и датасеты могут содержать чувствительные документы.

## Документация

- `accountant_tool/README.md` - запуск сервиса.
- `DEPLOYMENT_SIGNSTAMP.md` - заметки по переносу ML-окружения.
- `signature_stamp_cv_chat_summary.md` - техническое summary по задаче.
- `OTCHET_RABOTY_SIGNSTAMP.md` - отчет о выполненных работах для заказчика.
