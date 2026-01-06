api — принимает запросы
schemas — проверяет формат
services — принимает решения
models — хранит данные
payments — списывает деньги
tasks — работает по расписанию
security — защищает
utils — помогает
tests — подтверждает корректность



Клиент (frontend / curl / postman)
        │
        ▼
FastAPI endpoint (api/)
        │
        ▼
Pydantic schemas (schemas/)
        │
        ▼
Business logic (services/)
        │
        ├──► Payment Gateway (payments/)
        │         │
        │         ▼
        │     External system
        │     (Fake/YooMoney)
        │
        ▼
Database (models + SQLAlchemy)


Клиент отправляет HTTP-запрос
api/ принимает запрос (без логики)
schemas/ валидируют входные данные
services/ решают, что делать:
можно ли списывать
какой статус поставить
нужен ли retry
payments/ выполняет списание
models/ сохраняют результат в БД


