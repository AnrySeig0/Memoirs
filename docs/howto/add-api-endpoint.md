# How to: add an API endpoint

Routes never touch the DB. Data access goes through a **service**, which
delegates to a **repository**. Follow the layers top-down.

Example: add `GET /entities/{id}`.

### 1. Schema (`app/schemas/entity.py`)

```python
import uuid
from pydantic import BaseModel, ConfigDict

class EntityOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    kind: str
    canonical: str
```

Re-export it from `app/schemas/__init__.py`.

### 2. Repository (`app/repositories/entity.py`)

Stateless sync function; raise a domain exception on not-found:

```python
def get_entity(db: Session, entity_id: uuid.UUID) -> Entity:
    row = db.get(Entity, entity_id)
    if row is None:
        raise NotFoundError(f"entity {entity_id} not found")
    return row
```

(Use the model from `app/db/models/entity.py`.)

### 3. Service (`app/services/entity.py`)

```python
class EntityService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def get(self, entity_id: uuid.UUID) -> EntityOut:
        return EntityOut.model_validate(entity_repo.get_entity(self.db, entity_id))
```

### 4. DI alias (`app/api/deps.py`)

```python
def get_entity_service(db: DBSession) -> EntityService:
    return EntityService(db)

EntitySvc = Annotated[EntityService, Depends(get_entity_service)]
```

### 5. Route (`app/api/routes/v1/entities.py`)

```python
router = APIRouter(prefix="/entities", tags=["entities"])

@router.get("/{entity_id}", response_model=EntityOut)
def get_entity(entity_id: uuid.UUID, svc: EntitySvc) -> EntityOut:
    return svc.get(entity_id)            # no try/except; NotFoundError → 404
```

### 6. Register the router

Add `router` to `app/api/routes/v1/__init__.py`'s `v1_router`. Versioning is
a code boundary, not a URL prefix — keep paths at the root.

### 7. Test (`backend/tests/test_api_entities.py`)

Use the `api_client` fixture; assert `status_code` and the JSON body. 404
bodies come back as `{"detail": "..."}`.
