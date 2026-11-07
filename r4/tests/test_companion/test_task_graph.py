"""Tests for the task graph DAG."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from r4.companion.task_graph import TaskGraph


@pytest.fixture
def tmp_db():
    with tempfile.TemporaryDirectory() as tmp:
        yield str(Path(tmp) / "test.db")


@pytest.fixture
def tg(tmp_db):
    return TaskGraph(tmp_db)


def test_add_task_returns_task(tg):
    task = tg.add_task("Fix NaN issue")
    assert task.id
    assert task.title == "Fix NaN issue"
    assert task.status == "pending"
    assert task.progress == 0


def test_add_task_with_parent(tg):
    root = tg.add_task("Root")
    child = tg.add_task("Child", parent_id=root.id)
    assert child.parent_id == root.id
    # Verify parent's child_ids updated
    root_refreshed = tg.get_task(root.id)
    assert child.id in root_refreshed.child_ids


def test_add_task_with_dependencies(tg):
    search = tg.add_task("Search papers")
    patch = tg.add_task("Patch code", depends_on=[search.id])
    assert search.id in patch.depends_on


def test_update_task_status_done(tg):
    task = tg.add_task("Task")
    updated = tg.update_task_status(task.id, "done")
    assert updated is True
    refreshed = tg.get_task(task.id)
    assert refreshed.status == "done"
    assert refreshed.progress == 100
    assert refreshed.completed_at is not None


def test_update_task_status_failed(tg):
    task = tg.add_task("Task")
    tg.update_task_status(task.id, "failed")
    refreshed = tg.get_task(task.id)
    assert refreshed.status == "failed"
    assert refreshed.completed_at is not None


def test_update_task_progress(tg):
    task = tg.add_task("Task")
    tg.update_task_progress(task.id, 42)
    refreshed = tg.get_task(task.id)
    assert refreshed.progress == 42


def test_update_task_progress_clamps(tg):
    task = tg.add_task("Task")
    tg.update_task_progress(task.id, 150)
    assert tg.get_task(task.id).progress == 100
    tg.update_task_progress(task.id, -10)
    assert tg.get_task(task.id).progress == 0


def test_get_ready_tasks_no_deps(tg):
    _t1 = tg.add_task("Task 1")
    _t2 = tg.add_task("Task 2")
    ready = tg.get_ready_tasks()
    assert len(ready) == 2


def test_get_ready_tasks_with_deps(tg):
    search = tg.add_task("Search")
    patch = tg.add_task("Patch", depends_on=[search.id])
    # Initially, patch is not ready (search not done)
    ready = tg.get_ready_tasks()
    assert len(ready) == 1
    assert ready[0].id == search.id
    # Complete search
    tg.update_task_status(search.id, "done")
    ready = tg.get_ready_tasks()
    assert len(ready) == 1
    assert ready[0].id == patch.id


def test_get_ready_tasks_partial_deps(tg):
    t1 = tg.add_task("Task 1")
    t2 = tg.add_task("Task 2")
    _t3 = tg.add_task("Task 3", depends_on=[t1.id, t2.id])
    # t3 not ready (neither t1 nor t2 done)
    ready = tg.get_ready_tasks()
    assert len(ready) == 2  # t1 and t2
    # Complete t1 only
    tg.update_task_status(t1.id, "done")
    ready = tg.get_ready_tasks()
    assert len(ready) == 1  # t2 only
    # Complete t2
    tg.update_task_status(t2.id, "done")
    ready = tg.get_ready_tasks()
    assert len(ready) == 1  # t3 now ready


def test_get_root_tasks(tg):
    root1 = tg.add_task("Root 1")
    root2 = tg.add_task("Root 2")
    _child = tg.add_task("Child", parent_id=root1.id)
    roots = tg.get_root_tasks()
    assert len(roots) == 2
    root_ids = {r.id for r in roots}
    assert root1.id in root_ids
    assert root2.id in root_ids


def test_get_children(tg):
    root = tg.add_task("Root")
    c1 = tg.add_task("Child 1", parent_id=root.id)
    c2 = tg.add_task("Child 2", parent_id=root.id)
    children = tg.get_children(root.id)
    assert len(children) == 2
    child_ids = {c.id for c in children}
    assert c1.id in child_ids
    assert c2.id in child_ids


def test_list_all_tasks(tg):
    _t1 = tg.add_task("Task 1")
    _t2 = tg.add_task("Task 2")
    all_tasks = tg.list_all_tasks()
    assert len(all_tasks) == 2


def test_to_dict(tg):
    root = tg.add_task("Root")
    child = tg.add_task("Child", parent_id=root.id)
    d = tg.to_dict()
    assert root.id in d
    assert child.id in d
    assert d[root.id]["title"] == "Root"
    assert d[child.id]["parent_id"] == root.id


def test_get_nonexistent_task(tg):
    assert tg.get_task("nonexistent") is None


def test_update_nonexistent_task(tg):
    assert tg.update_task_status("nonexistent", "done") is False
    assert tg.update_task_progress("nonexistent", 50) is False
