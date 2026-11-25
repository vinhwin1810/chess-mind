"""Create the chess analysis schema and seed the local demo identity."""

from datetime import datetime, timezone
from uuid import UUID

from alembic import op
import sqlalchemy as sa

revision = "0001_core"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('users',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('email', sa.String(length=254), nullable=False),
        sa.Column('display_name', sa.String(length=100), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('email')
    )
    op.create_table('games',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('owner_id', sa.Uuid(), nullable=False),
        sa.Column('source', sa.String(length=16), nullable=False),
        sa.Column('source_game_url', sa.Text(), nullable=True),
        sa.Column('external_id', sa.Text(), nullable=True),
        sa.Column('pgn', sa.Text(), nullable=False),
        sa.Column('white_username', sa.Text(), nullable=True),
        sa.Column('black_username', sa.Text(), nullable=True),
        sa.Column('user_color', sa.String(length=5), nullable=True),
        sa.Column('time_class', sa.String(length=20), nullable=True),
        sa.Column('time_control', sa.Text(), nullable=True),
        sa.Column('result', sa.String(length=7), nullable=False),
        sa.Column('played_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('canonical_hash', sa.String(length=64), nullable=False),
        sa.ForeignKeyConstraint(['owner_id'], ['users.id'], ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('owner_id', 'canonical_hash')
    )
    op.create_index('ix_games_owner_created', 'games', ['owner_id', 'created_at', 'id'], unique=False)
    op.create_table('analysis_jobs',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('requesting_user_id', sa.Uuid(), nullable=False),
        sa.Column('game_id', sa.Uuid(), nullable=False),
        sa.Column('status', sa.String(length=16), nullable=False),
        sa.Column('attempt_count', sa.Integer(), nullable=False),
        sa.Column('engine_version', sa.Text(), nullable=True),
        sa.Column('depth', sa.Integer(), nullable=False),
        sa.Column('error_code', sa.String(length=40), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('lease_until', sa.DateTime(timezone=True), nullable=True),
        sa.Column('next_dispatch_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('claim_token', sa.Uuid(), nullable=True),
        sa.Column('idempotency_key', sa.String(length=80), nullable=False),
        sa.ForeignKeyConstraint(['game_id'], ['games.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['requesting_user_id'], ['users.id'], ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('requesting_user_id', 'game_id', 'idempotency_key')
    )
    op.create_index(op.f('ix_analysis_jobs_game_id'), 'analysis_jobs', ['game_id'], unique=False)
    op.create_index('ix_jobs_owner_status', 'analysis_jobs', ['requesting_user_id', 'status'], unique=False)
    op.create_index('ix_jobs_status_lease', 'analysis_jobs', ['status', 'lease_until'], unique=False)
    op.create_table('moves',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('game_id', sa.Uuid(), nullable=False),
        sa.Column('ply', sa.Integer(), nullable=False),
        sa.Column('uci', sa.String(length=8), nullable=False),
        sa.Column('san', sa.String(length=24), nullable=False),
        sa.Column('fen_before', sa.Text(), nullable=False),
        sa.Column('fen_after', sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(['game_id'], ['games.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('game_id', 'ply')
    )
    op.create_table('move_evaluations',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('game_id', sa.Uuid(), nullable=False),
        sa.Column('analysis_job_id', sa.Uuid(), nullable=False),
        sa.Column('ply', sa.Integer(), nullable=False),
        sa.Column('engine_version', sa.Text(), nullable=False),
        sa.Column('depth', sa.Integer(), nullable=False),
        sa.Column('score_before', sa.Integer(), nullable=True),
        sa.Column('score_after', sa.Integer(), nullable=True),
        sa.Column('mate_before', sa.Integer(), nullable=True),
        sa.Column('mate_after', sa.Integer(), nullable=True),
        sa.Column('best_move_uci', sa.String(length=8), nullable=True),
        sa.Column('pv_uci', sa.JSON(), nullable=False),
        sa.Column('cp_loss', sa.Integer(), nullable=True),
        sa.Column('position_metadata', sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(['analysis_job_id'], ['analysis_jobs.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['game_id'], ['games.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('analysis_job_id', 'ply')
    )
    op.create_index(op.f('ix_move_evaluations_game_id'), 'move_evaluations', ['game_id'], unique=False)
    users = sa.table("users", sa.column("id", sa.Uuid()), sa.column("email", sa.String()),
                     sa.column("display_name", sa.String()), sa.column("created_at", sa.DateTime(timezone=True)))
    op.bulk_insert(users, [dict(id=UUID("00000000-0000-4000-8000-000000000001"),
                                email="demo@example.invalid", display_name="Local demo",
                                created_at=datetime(2025, 1, 1, tzinfo=timezone.utc))])


def downgrade():
    op.drop_index(op.f('ix_move_evaluations_game_id'), table_name='move_evaluations')
    op.drop_table('move_evaluations')
    op.drop_table('moves')
    op.drop_index('ix_jobs_status_lease', table_name='analysis_jobs')
    op.drop_index('ix_jobs_owner_status', table_name='analysis_jobs')
    op.drop_index(op.f('ix_analysis_jobs_game_id'), table_name='analysis_jobs')
    op.drop_table('analysis_jobs')
    op.drop_index('ix_games_owner_created', table_name='games')
    op.drop_table('games')
    op.drop_table('users')
