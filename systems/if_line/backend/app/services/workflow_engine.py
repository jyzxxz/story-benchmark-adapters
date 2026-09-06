"""
工作流引擎 - 管理项目状态流转
"""
from typing import Dict, Any, Optional
from sqlalchemy.orm import Session
from app.models import Project, WorkflowRun, ProjectStatus
from datetime import datetime
import json


class WorkflowEngine:
    """工作流引擎"""

    # 状态流转顺序
    STATUS_FLOW = [
        ProjectStatus.draft_input,
        ProjectStatus.bible_generated,
        ProjectStatus.outline_generated,
        ProjectStatus.outline_reviewing,
        ProjectStatus.outline_approved,
        ProjectStatus.chapter_generating,
        ProjectStatus.asset_generating,
        ProjectStatus.vn_graph_generating,
        ProjectStatus.reading,
        ProjectStatus.pre_generating,
        ProjectStatus.completed,
    ]

    def __init__(self, db: Session):
        self.db = db

    def get_current_step(self, project: Project) -> str:
        """获取当前步骤"""
        return project.status

    def get_next_step(self, project: Project) -> Optional[str]:
        """获取下一步骤"""
        current_index = self.STATUS_FLOW.index(ProjectStatus(project.status))
        if current_index < len(self.STATUS_FLOW) - 1:
            return self.STATUS_FLOW[current_index + 1].value
        return None

    def can_proceed(self, project: Project, target_step: str) -> bool:
        """检查是否可以进入目标步骤"""
        current_index = self.STATUS_FLOW.index(ProjectStatus(project.status))
        target_index = self.STATUS_FLOW.index(ProjectStatus(target_step))

        # 只能顺序执行
        return target_index == current_index + 1

    def transition_to(
        self,
        project: Project,
        target_step: str,
        input_json: Optional[Dict[str, Any]] = None,
        output_json: Optional[Dict[str, Any]] = None
    ) -> WorkflowRun:
        """转换到下一步骤"""
        # 创建工作流记录
        workflow_run = WorkflowRun(
            project_id=project.id,
            current_step=target_step,
            input_json=input_json,
            output_json=output_json,
            status="running"
        )
        self.db.add(workflow_run)

        # 更新项目状态
        project.status = target_step
        project.updated_at = datetime.utcnow()

        self.db.commit()
        self.db.refresh(workflow_run)

        return workflow_run

    def complete_step(
        self,
        workflow_run: WorkflowRun,
        output_json: Optional[Dict[str, Any]] = None
    ):
        """完成当前步骤"""
        workflow_run.status = "completed"
        workflow_run.output_json = output_json
        workflow_run.updated_at = datetime.utcnow()
        self.db.commit()

    def fail_step(
        self,
        workflow_run: WorkflowRun,
        error_message: str
    ):
        """标记步骤失败"""
        workflow_run.status = "failed"
        workflow_run.error_message = error_message
        workflow_run.updated_at = datetime.utcnow()
        self.db.commit()

    def get_workflow_runs(self, project_id: int) -> list:
        """获取项目的工作流记录"""
        return self.db.query(WorkflowRun).filter(
            WorkflowRun.project_id == project_id
        ).order_by(WorkflowRun.created_at.desc()).all()

    def get_progress(self, project: Project) -> Dict[str, Any]:
        """获取项目进度"""
        current_index = self.STATUS_FLOW.index(ProjectStatus(project.status))

        return {
            "current_step": project.status,
            "current_step_index": current_index,
            "total_steps": len(self.STATUS_FLOW),
            "progress_percentage": (current_index / (len(self.STATUS_FLOW) - 1)) * 100,
            "completed_steps": [step.value for step in self.STATUS_FLOW[:current_index]],
            "pending_steps": [step.value for step in self.STATUS_FLOW[current_index + 1:]]
        }
