-- PostgreSQL foundation for hosted Archie in ap-southeast-2.
-- The application always checks memberships; these constraints prevent
-- duplicate or invalid role records at the persistence boundary.

CREATE TABLE app_users (
    cognito_subject UUID PRIMARY KEY,
    email TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE projects (
    project_id UUID PRIMARY KEY,
    owner_subject UUID NOT NULL REFERENCES app_users(cognito_subject),
    name TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    deleted_at TIMESTAMPTZ
);

CREATE TABLE project_memberships (
    project_id UUID NOT NULL REFERENCES projects(project_id) ON DELETE CASCADE,
    cognito_subject UUID NOT NULL REFERENCES app_users(cognito_subject) ON DELETE CASCADE,
    role TEXT NOT NULL CHECK (role IN ('owner', 'editor', 'viewer')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (project_id, cognito_subject)
);

CREATE TABLE project_artifacts (
    artifact_id UUID PRIMARY KEY,
    project_id UUID NOT NULL REFERENCES projects(project_id) ON DELETE CASCADE,
    object_key TEXT NOT NULL UNIQUE,
    media_type TEXT NOT NULL,
    sha256 CHAR(64) NOT NULL,
    byte_size BIGINT NOT NULL CHECK (byte_size >= 0),
    classification TEXT NOT NULL CHECK (classification IN ('source_pdf', 'evidence', 'report', 'archive')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX project_memberships_subject_idx ON project_memberships(cognito_subject, project_id);
CREATE INDEX project_artifacts_project_idx ON project_artifacts(project_id, artifact_id);
