"""学习空间的业务逻辑。"""

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Workspace, WorkspaceMember

# workspace_members.role 的取值来自 models.WORKSPACE_ROLES，这里是其中最常用的那个。
ROLE_OWNER = "owner"


async def create_workspace_with_owner(
    session: AsyncSession,
    *,
    name: str,
    owner_id: uuid.UUID,
) -> Workspace:
    """建一个学习空间，并把创建者写为 owner。

    **建空间只能通过这个函数，不要把两次写入拆开调用。** 原因：

    「谁是空间的主人」这件事在库里存了两份——`workspaces.owner_id` 和
    `workspace_members` 里 `role='owner'` 的那一行。这是设计上已知的冗余，
    两边都有存在的理由：
    - `owner_id` 必须有，因为 DR-06 要求删用户时级联删掉其空间，
      数据库需要一条从 workspaces 直达 users 的外键才能知道该删哪些；
    - 成员行也必须有，因为权限模型是按 workspace_members 判定的（FR-WS-03/04）。

    数据库只能挡住「同一空间出现两个 owner 行」（部分唯一索引
    uq_workspace_members_single_owner），挡不住「写了 owner_id 却漏了成员行」
    这种漏写。所以一致性只能靠应用层保证，而可靠的做法就是让两处写入
    只在一个地方发生。

    **本函数不 commit**：事务边界由调用方控制，这样它才能和调用方的其他写入
    （比如注册时的建用户）落在同一个事务里，要么全成要么全不成。
    """
    workspace = Workspace(name=name, owner_id=owner_id)
    session.add(workspace)

    # 必须先 flush 才能拿到 workspace.id——主键是应用层的 default=uuid.uuid4，
    # 构造对象时还没生成，只有 flush 到数据库前才由 SQLAlchemy 填上。
    await session.flush()

    session.add(
        WorkspaceMember(workspace_id=workspace.id, user_id=owner_id, role=ROLE_OWNER)
    )

    return workspace
