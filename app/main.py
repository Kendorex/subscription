from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from api.routes.plans import router as plans_router, admin_router as admin_plans_router
from api.routes.payment_methods import router as pm_router
from api.routes.subscriptions import router as subs_router
from api.routes.admin_ops import router as admin_ops_router
from security.register import router as dev_auth_router
app = FastAPI(title="Subscription MVP")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # для теста
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(dev_auth_router)
app.include_router(plans_router)
app.include_router(admin_plans_router)
app.include_router(pm_router)
app.include_router(subs_router)
app.include_router(admin_ops_router)
