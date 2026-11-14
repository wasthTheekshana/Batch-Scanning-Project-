"""Admin: versioned project/template config, push/assign, global settings."""
import json
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from central.audit import record
from central.config_rules import check_config
from central.deps import get_db, require_admin, require_csrf
from central.models import (AdminUser, ConfigVersion, Pc, PcAssignment, Project,
                            Template)
from central.settings_store import get_poll_interval, set_poll_interval
from central.versioning import save_version
from projects.profile_manager import (DEFAULT_PROJECT, ProfileManager,
                                  ProjectConfigError)

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


@router.get("/project-defaults")
def project_defaults():
    """A complete, valid starting configuration for a new project (the
    admin fills in what differs; identity fields are set on save)."""
    config = json.loads(json.dumps(DEFAULT_PROJECT))
    for key in ("project_id", "name", "created", "cloned_from"):
        config.pop(key, None)
    return config


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
                renamed = (row.name, row.category) != (body.name, body.category)
                row.name, row.category = body.name, body.category
                changed = save_version(db, "template", row, body.config,
                                       f"admin:{admin.username}")
                if changed:
                    record(db, f"admin:{admin.username}", "template_saved",
                           {"template_id": template_id, "version": row.current_version})
                elif renamed:
                    record(db, f"admin:{admin.username}", "template_updated",
                           {"template_id": template_id, "name": body.name,
                            "category": body.category})
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


@router.get("/templates/{template_id}/versions")
def template_versions(template_id: TemplateId, db: Session = Depends(get_db)):
    t = db.get(Template, template_id)
    if t is None:
        raise HTTPException(404, "unknown template")
    return [{"version": v.version, "created_by": v.created_by,
             "created_at": v.created_at.isoformat() + "Z"}
            for v in _history(db, "template", template_id)]


@router.get("/templates/{template_id}/versions/{version}")
def template_version(version: int, template_id: TemplateId,
                    db: Session = Depends(get_db)):
    t = db.get(Template, template_id)
    if t is None:
        raise HTTPException(404, "unknown template")
    v = db.scalar(
        select(ConfigVersion).where(ConfigVersion.kind == "template",
                                    ConfigVersion.owner_id == template_id,
                                    ConfigVersion.version == version))
    if v is None:
        raise HTTPException(404, "unknown version")
    return {"version": v.version, "config": json.loads(v.config_json)}


# -- push / assignments -------------------------------------------------------
class PushIn(BaseModel):
    pc_ids: list[str] = Field(default_factory=list)
    all_pcs: bool = False


@router.post("/projects/{project_id}/push")
def push_project(body: PushIn, project_id: ProjectId,
                 admin: AdminUser = Depends(require_admin),
                 db: Session = Depends(get_db)):
    if body.all_pcs == bool(body.pc_ids):
        raise HTTPException(422, "give either pc_ids or all_pcs=true, not both/neither")
    _project(db, project_id)
    if body.all_pcs:
        target = list(db.scalars(select(Pc.id)))
    else:
        target = list(dict.fromkeys(body.pc_ids))
        known = set(db.scalars(select(Pc.id).where(Pc.id.in_(target))))
        missing = [p for p in target if p not in known]
        if missing:
            raise HTTPException(404, f"unknown PC(s): {', '.join(missing)}")
    # A concurrent push may insert some of the same (pc, project) rows between
    # our read and commit. The rollback discards every pending insert, so
    # re-read what exists and retry for the PCs still missing.
    for attempt in range(3):
        have = set(db.scalars(select(PcAssignment.pc_id).where(
            PcAssignment.project_id == project_id)))
        for pc_id in target:
            if pc_id not in have:
                db.add(PcAssignment(pc_id=pc_id, project_id=project_id))
        record(db, f"admin:{admin.username}", "project_pushed",
               {"project_id": project_id, "pc_ids": target})
        try:
            db.commit()
            break
        except IntegrityError:
            db.rollback()
    else:
        raise HTTPException(409, "concurrent update, retry")
    return {"assigned": target}


@router.delete("/projects/{project_id}/assignments/{pc_id}", status_code=204)
def unassign(pc_id: str, project_id: ProjectId,
             admin: AdminUser = Depends(require_admin),
             db: Session = Depends(get_db)):
    result = db.execute(delete(PcAssignment).where(
        PcAssignment.project_id == project_id, PcAssignment.pc_id == pc_id))
    if result.rowcount == 0:
        raise HTTPException(404, "no such assignment")
    record(db, f"admin:{admin.username}", "project_unassigned",
           {"project_id": project_id, "pc_id": pc_id})
    db.commit()
    return Response(status_code=204)


# -- global settings ------------------------------------------------------------
class SettingsIn(BaseModel):
    poll_interval_seconds: int = Field(ge=30, le=86400, strict=True)


@router.get("/settings")
def get_settings(request: Request, db: Session = Depends(get_db)):
    default = request.app.state.settings.default_poll_interval_seconds
    return {"poll_interval_seconds": get_poll_interval(db, default)}


@router.put("/settings")
def put_settings(body: SettingsIn,
                 admin: AdminUser = Depends(require_admin),
                 db: Session = Depends(get_db)):
    set_poll_interval(db, body.poll_interval_seconds)
    record(db, f"admin:{admin.username}", "settings_changed",
           {"poll_interval_seconds": body.poll_interval_seconds})
    db.commit()
    return {"poll_interval_seconds": body.poll_interval_seconds}
