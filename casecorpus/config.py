"""Configuration: working directory layout and credentials from the environment.

Nothing here is secret by default. Credentials are read from environment variables
(or a .env file in the working directory) and never written to the database.

Environment variables
---------------------
CASECORPUS_HOME      working directory (default: ./casecorpus-work)
NCBI_API_KEY         PubMed E-utilities key (10 req/s instead of 3)
NCBI_EMAIL           contact e-mail sent with E-utilities and Unpaywall requests
UNPAYWALL_EMAIL      e-mail for the Unpaywall API (defaults to NCBI_EMAIL)
ELSEVIER_API_KEY     publisher TDM tier (optional)
ELSEVIER_INSTTOKEN   publisher TDM tier (optional; institutional token)
WILEY_TDM_TOKEN      publisher TDM tier (optional; Crossref click-through token)
SPRINGER_API_KEY     publisher TDM tier (optional)
ANTHROPIC_API_KEY    only needed for the API-based extractor
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


@dataclass
class Settings:
    home: Path = field(default_factory=lambda: Path(os.environ.get("CASECORPUS_HOME", "casecorpus-work")).resolve())
    ncbi_api_key: str | None = None
    email: str | None = None
    unpaywall_email: str | None = None
    elsevier_api_key: str | None = None
    elsevier_insttoken: str | None = None
    wiley_tdm_token: str | None = None
    springer_api_key: str | None = None
    anthropic_api_key: str | None = None

    def __post_init__(self) -> None:
        _load_dotenv(Path.cwd() / ".env")
        _load_dotenv(self.home / ".env")
        self.ncbi_api_key = self.ncbi_api_key or os.environ.get("NCBI_API_KEY")
        self.email = self.email or os.environ.get("NCBI_EMAIL")
        self.unpaywall_email = self.unpaywall_email or os.environ.get("UNPAYWALL_EMAIL") or self.email
        self.elsevier_api_key = self.elsevier_api_key or os.environ.get("ELSEVIER_API_KEY")
        self.elsevier_insttoken = self.elsevier_insttoken or os.environ.get("ELSEVIER_INSTTOKEN")
        self.wiley_tdm_token = self.wiley_tdm_token or os.environ.get("WILEY_TDM_TOKEN")
        self.springer_api_key = self.springer_api_key or os.environ.get("SPRINGER_API_KEY")
        self.anthropic_api_key = self.anthropic_api_key or os.environ.get("ANTHROPIC_API_KEY")

    # --- layout -----------------------------------------------------------
    @property
    def db_path(self) -> Path:
        return self.home / "catalogue.sqlite"

    @property
    def ontologies(self) -> Path:
        return self.home / "ontologies"

    @property
    def scope_dir(self) -> Path:
        return self.home / "scope"

    @property
    def raw_dir(self) -> Path:
        """Raw retrieved documents: raw/<pmid>/{pubmed.xml, epmc.xml, unpaywall.pdf, ...}"""
        return self.home / "raw"

    @property
    def work_dir(self) -> Path:
        """Extraction exchange: work/<pmid>/{input.md, manifest.json, output.json}"""
        return self.home / "work"

    @property
    def records_dir(self) -> Path:
        """Validated per-individual records and phenopackets."""
        return self.home / "records"

    @property
    def export_dir(self) -> Path:
        return self.home / "export"

    def ensure(self) -> "Settings":
        for p in (self.home, self.ontologies, self.scope_dir, self.raw_dir, self.work_dir, self.records_dir, self.export_dir):
            p.mkdir(parents=True, exist_ok=True)
        return self


settings = Settings()
