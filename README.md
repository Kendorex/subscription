# Subscription Billing Service

Мини-сервис подписок с кошельком (balance ledger), авто-списаниями, ретраями платежей, уведомлениями (email/in-app) и scheduled задачами через Celery.

---

## Возможности

### Подписки
- Создание подписки с учётом **trial только 1 раз на аккаунт** (`User.trial_used_at`) :contentReference[oaicite:0]{index=0}  
- Статусы подписки: `TRIAL / ACTIVE / PAST_DUE / CANCELED / EXPIRED` (и переходы по времени)   
- Отмена:
  - **Cancel at period end**: подписка работает до конца оплаченного периода, затем становится `EXPIRED`   
  - **Cancel immediately**: мгновенная отмена (`CANCELED`) :contentReference[oaicite:3]{index=3}  

### Биллинг и платежи
- Авто-списания по подписке (проверка “due”, создание invoice/transaction, попытка charge)   
- **Idempotency keys** на списания/инвойсы/транзакции, включая ключ по периоду (`period_key`) :contentReference[oaicite:5]{index=5}  
- Ретраи платежей при временной недоступности/decline (ограничение попыток + backoff)   
- Поддержка **режима оплаты**:
  - `balance` — списание с внутреннего кошелька
  - `card` — списание через payment gateway (по default payment method)   

### Кошелёк 
- Хранение баланса в `User.balance_cents` + журнал проводок `BalanceEntry` :contentReference[oaicite:8]{index=8}  
- `credit()` / `debit_if_possible()` с блокировкой пользователя и идемпотентностью :contentReference[oaicite:9]{index=9}  

### Уведомления
- Очередь уведомлений в БД: статус `PENDING/SENT/FAILED`, `scheduled_at`, ретраи   
- Каналы:
  - `EMAIL` (SMTP, либо dev-fallback печатает письмо в лог, если SMTP не настроен) :contentReference[oaicite:11]{index=11}  
  - `IN_APP` (заглушка) :contentReference[oaicite:12]{index=12}  
- Throttle/anti-spam для частых `PAYMENT_FAIL` (окно 10 минут) :contentReference[oaicite:13]{index=13}  

### Scheduled tasks (Celery)
- `billing` — каждые ~30 секунд проверяет подписки “к списанию”   
- `notifications` — каждые ~60 секунд отправляет due-уведомления   

---

## Архитектура 

- **Models** (SQLAlchemy): `User`, `Plan`, `Subscription`, `Invoice`, `Transaction`, `PaymentMethod`, `BalanceEntry`, `Notification`, `Refund` и др. (см. `models/`)  
- **Services**
  - `SubscriptionService` — создание/отмена/переходы статусов :contentReference[oaicite:16]{index=16}  
  - `BillingService` — списание, инвойсы/транзакции, ретраи, идемпотентность :contentReference[oaicite:17]{index=17}  
  - `WalletService` — кошелёк и ledger проводок :contentReference[oaicite:18]{index=18}  
  - `NotificationService` — enqueue + отправка SMTP + ретраи :contentReference[oaicite:19]{index=19}  
- **Payments**
  - `PaymentGateway` (Protocol) + типовые ошибки :contentReference[oaicite:20]{index=20}  
  - `FakePaymentGateway` (рандомизирует исходы: success/insufficient/unavailable/declined) :contentReference[oaicite:21]{index=21}  
- **Tasks**
  - `run_billing_due` — джоба биллинга :contentReference[oaicite:22]{index=22}  
  - `send_due_notifications` — джоба отправки уведомлений :contentReference[oaicite:23]{index=23}  
  - Планировщик beat — в `celery_app.py` :contentReference[oaicite:24]{index=24}  

## 🚀 Быстрый старт

### Запуск через Docker

Проект полностью запускается через **Docker Compose** и не требует ручного старта сервисов.

#### Требования
- Docker ≥ 20.x  
- Docker Compose ≥ 2.x  

#### Запуск
**docker compose up -d --build**
