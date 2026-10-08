"""Admin: versioned project/template config (push and settings: Task 7)."""
import json
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from central.audit import record
from central.config_rules import check_config
from central.deps import get_db, require_admin, require_csrf
from central.models import (AdminUser, ConfigVersion, Project, Template)
from central.versioning import save_version
from core.profile_manager import ProfileManager, ProjectConfigError

router = APIRouter(prefix="/admin", tags=["admin-config"],
                   dependencies=[Depends(require_admin),
                                 Depends(require_csrf)])

ProjectId = Annotated[str, Path(pattern=r"^[A-Za-z0-9-]{1,36}$")]
TemplateId = Annotated[str, Path(pattern=r"^[A-Za-z0-9-]{1,36}$")]


class ProjectIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    config: dict


class TemplateIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    category: str = Field(default="", max_length=100)
    config: dict


def _check(config: dict) -> None:
    try:
        check_config(config)
    except ValueError as e:
        raise HTTPException(422, str(e))


def _history(db: Session, kind: str, owner_id: str):
    return list(db.scalars(
        select(ConfigVersion).where(ConfigVersion.kind == kind,
                                    ConfigVersion.owner_id == owner_id)
        .order_by(ConfigVersion.version.desc())))


# -- projects ---------------------------------------------------------------
@router.put("/projects/{project_id}")
def put_project(body: ProjectIn, project_id: ProjectId,
                admin: AdminUser = Depends(require_admin),
                db: Session = Depends(get_db)):
    try:
        config = {**body.config, "project_id": project_id, "name": body.name}
        _check(config)
        try:
            ProfileManager.validate(config)
        except ProjectConfigError as e:
            raise HTTPException(422, str(e))

        # Retry loop to handle concurrent create/update races
        for attempt in range(2):
            try:
                row = db.get(Project, project_id)
                if row is None:
                    row = Project(id=project_id, name=body.name, config_json="{}",
                                  current_version=0)
                    db.add(row)
                row.name = body.name
                changed = save_version(db, "project", row, config,
                                       f"admin:{admin.username}")
                if changed:
                    record(db, f"admin:{admin.username}", "project_saved",
                           {"project_id": project_id, "version": row.current_version})
                db.commit()
                return {"id": row.id, "name": row.name, "version": row.current_version,
                        "changed": changed}
            except IntegrityError:
                db.rollback()
                if attempt == 1:
                    # Second attempt also failed
                    raise HTTPException(409, "concurrent update, retry")
                # Retry from the top of the loop
    except RecursionError:
        raise HTTPException(422, "config nested too deeply")


@router.get("/projects")
def list_projects(db: Session = Depends(get_db)):
    return [{"id": p.id, "name": p.name, "version": p.current_version}
            for p in db.scalars(select(Project).order_by(Project.name))]


def _project(db: Session, project_id: str) -> Project:
    row = db.get(Project, project_id)
    if row is None:
        raise HTTPException(404, "unknown project")
    return row


@router.get("/projects/{project_id}")
def get_project(project_id: ProjectId, db: Session = Depends(get_db)):
    p = _project(db, project_id)
    return {"id": p.id, "name": p.name, "version": p.current_version,
            "config": json.loads(p.config_json)}


@router.get("/projects/{project_id}/versions")
def project_versions(project_id: ProjectId, db: Session = Depends(get_db)):
    _project(db, project_id)
    return [{"version": v.version, "created_by": v.created_by,
             "created_at": v.created_at.isoformat() + "Z"}
            for v in _history(db, "project", project_id)]


@router.get("/projects/{project_id}/versions/{version}")
def project_version(version: int, project_id: ProjectId,
                    db: Session = Depends(get_db)):
    _project(db, project_id)
    v = db.scalar(
        select(ConfigVersion).where(ConfigVersion.kind == "project",
                                    ConfigVersion.owner_id == project_id,
                                    ConfigVersion.version == version))
    if v is None:
        raise HTTPException(404, "unknown version")
    return {"version": v.version, "config": json.loads(v.config_json)}


# -- templates --------------------------------------------------------------
@router.put("/templates/{template_id}")
def put_template(body: TemplateIn, template_id: TemplateId,
                 admin: AdminUser = Depends(require_admin),
                 db: Session = Depends(get_db)):
    try:
        _check(body.config)

        # Retry loop to handle concurrent create/update races
        for attempt in range(2):
            try:
                row = db.get(Template, template_id)
                if row is None:
                    row = Template(id=template_id, name=body.name, category=body.category,
                                   config_json="{}", built_in=False, current_version=0)
                    db.add(row)
                row.name, row.category = body.name, body.category
                changed = save_version(db, "template", row, body.config,
                                       f"admin:{admin.username}")
                if changed:
                    record(db, f"admin:{admin.username}", "template_saved",
                           {"template_id": template_id, "version": row.current_version})
                db.commit()
                return {"id": row.id, "name": row.name, "version": row.current_version,
                        "changed": changed}
            except IntegrityError:
                db.rollback()
                if attempt == 1:
                    # Second attempt also failed
                    raise HTTPException(409, "concurrent update, retry")
                # Retry from the top of the loop
    except RecursionError:
        raise HTTPException(422, "config nested too deeply")


@router.get("/templates")
def list_templates(db: Session = Depends(get_db)):
    return [{"id": t.id, "name": t.name, "category": t.category,
             "built_in": t.built_in, "version": t.current_version}
            for t in db.scalars(select(Template).order_by(Template.name))]


@router.get("/templates/{template_id}")
def get_template(template_id: TemplateId, db: Session = Depends(get_db)):
    t = db.get(Template, template_id)
    if t is None:
        raise HTTPException(404, "unknown template")
    return {"id": t.id, "name": t.name, "category": t.category,
            "built_in": t.built_in, "version": t.current_version,
            "config": json.loads(t.config_json)}
