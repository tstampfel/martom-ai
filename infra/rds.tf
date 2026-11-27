# RDS Postgres 16 in the private subnets, single AZ, the smallest Graviton class.
#
# Extensions the migrations create: unaccent (001, 006), pg_trgm (006) and vector (007,
# which needs pgvector >= 0.8 for hnsw.iterative_scan). RDS for PostgreSQL ships all three;
# pgvector 0.8.0 came with minor version 16.5, and RDS's default 16.x minor is later than
# that. Migration 007 checks the installed pgvector version itself and fails loudly if it
# is too old. The master user is rds_superuser, so `martom migrate` can CREATE EXTENSION.

resource "random_password" "db" {
  length  = 32
  special = false # the password goes into DATABASE_URL, so keep it URL-safe
}

resource "aws_db_subnet_group" "main" {
  name       = local.prefix
  subnet_ids = aws_subnet.private[*].id
}

resource "aws_db_instance" "main" {
  identifier     = local.prefix
  engine         = "postgres"
  engine_version = var.db_engine_version
  instance_class = var.db_instance_class

  db_name  = "martom"
  username = "martom"
  password = random_password.db.result
  port     = 5432

  allocated_storage     = var.db_allocated_storage
  max_allocated_storage = var.db_max_allocated_storage
  storage_type          = "gp3"
  storage_encrypted     = true

  db_subnet_group_name   = aws_db_subnet_group.main.name
  vpc_security_group_ids = [aws_security_group.db.id]
  publicly_accessible    = false
  # One AZ: a Multi-AZ standby would double the database cost. An AZ outage means
  # downtime until AWS recovers it; the nightly backups cover data loss.
  multi_az = false

  backup_retention_period  = var.db_backup_retention_days
  # Times are UTC (local time is UTC+1, UTC+2 in summer): after the nightly job has
  # finished (it starts at 01:00 local and stops by its 3 h deadline) and before the shop
  # opens. Maintenance (which may restart the database) avoids Monday, the weekly full run.
  backup_window      = "04:00-04:30"
  maintenance_window = "wed:04:30-wed:05:30"
  copy_tags_to_snapshot    = true
  delete_automated_backups = false # keep them for their retention period after a destroy

  auto_minor_version_upgrade  = true
  allow_major_version_upgrade = false

  # Postgres 15+ on RDS requires TLS by default (rds.force_ssl = 1); DATABASE_URL uses
  # sslmode=require to match.
  enabled_cloudwatch_logs_exports = ["postgresql"]

  deletion_protection       = var.db_deletion_protection
  skip_final_snapshot       = false
  final_snapshot_identifier = "${local.prefix}-final"
}
