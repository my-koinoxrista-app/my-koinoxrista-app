"""Run the bundled candidate against disposable PostgreSQL, without touching live files."""
import hashlib
import os
from pathlib import Path, PurePosixPath
import subprocess
import sys
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]
ARCHIVE = ROOT / 'koinoxrista_tenancy_candidate.zip'
EXPECTED_SHA256 = '0800ae5748ab6f50d00cc5df6da0177bf24b140576773851d2d02ff0bfd02472'

def main():
    if not ARCHIVE.is_file():
        raise SystemExit('Missing koinoxrista_tenancy_candidate.zip beside scripts/.')
    if hashlib.sha256(ARCHIVE.read_bytes()).hexdigest() != EXPECTED_SHA256:
        raise SystemExit('Candidate archive checksum mismatch. No test started.')
    # Fail before creating a container if the current environment lacks dependencies.
    import psycopg
    import streamlit
    import fpdf
    import openai
    subprocess.run(['docker','info','--format','{{.ServerVersion}}'],check=True,stdout=subprocess.DEVNULL)
    with tempfile.TemporaryDirectory(prefix='koinoxrista-tenancy-') as tmp:
        work=Path(tmp)
        with zipfile.ZipFile(ARCHIVE) as z:
            for info in z.infolist():
                path=PurePosixPath(info.filename)
                if path.is_absolute() or '..' in path.parts or not path.parts:
                    raise SystemExit('Unsafe archive path. No test started.')
                if info.is_dir(): continue
                # Reject symbolic links and other non-regular archive entries.
                kind=(info.external_attr >> 16) & 0o170000
                if kind not in (0,0o100000):
                    raise SystemExit('Unsafe archive entry. No test started.')
                target=work.joinpath(*path.parts)
                target.parent.mkdir(parents=True,exist_ok=True)
                target.write_bytes(z.read(info))
        # No live database DSN, password, dotenv, API key or signing key is inherited.
        env={k:v for k,v in os.environ.items() if not (
            k.startswith(('POSTGRES_','PG','KOINOXRISTA_','OPENAI_'))
            or k in ('DATABASE_URL','PYTHONPATH','PYTHONHOME','ENV_FILE','DOTENV_PATH')
        )}
        env['PYTHONNOUSERSITE']='1'
        env['PYTHONDONTWRITEBYTECODE']='1'
        env['PYTHONPATH']=str(work)
        print('Testing a temporary source copy. The live project and database are not targets.',flush=True)
        result=subprocess.run([sys.executable,'-I',str(work/'scripts/run_tenancy_tests.py')],
                              cwd=work,env=env,check=False)
        if result.returncode:
            raise SystemExit(result.returncode)
        print('Disposable test completed. No live migration has been applied.')

if __name__=='__main__':
    main()
