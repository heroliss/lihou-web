"""Install a checksum-verified static /works/ build, keeping the previous build."""
import argparse
import hashlib
from pathlib import Path, PurePosixPath
import re
import shutil
import tempfile
import time
from urllib.request import urlopen
from zipfile import ZipFile


def install(url, expected_sha, destination):
    if not any(url.startswith(f'https://github.com/heroliss/{name}/releases/download/')
               for name in ('liss-community', 'liss-web')):
        raise ValueError('Use a public Liss Web GitHub Release asset.')
    if not re.fullmatch('[a-fA-F0-9]{64}', expected_sha):
        raise ValueError('A SHA-256 digest is required.')
    destination=destination.expanduser().resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='liss-portfolio-', dir=destination.parent) as temporary:
        temporary=Path(temporary)
        archive=temporary/'build.zip'
        digest=hashlib.sha256()
        total=0
        with urlopen(url,timeout=60) as response, archive.open('wb') as output:
            while block:=response.read(1024*1024):
                total+=len(block)
                if total>50*1024*1024:
                    raise ValueError('Portfolio archive exceeds the 50 MiB limit.')
                digest.update(block)
                output.write(block)
        if digest.hexdigest()!=expected_sha.lower():
            raise ValueError('Portfolio archive SHA-256 mismatch.')
        stage=temporary/'ready'
        stage.mkdir()
        with ZipFile(archive) as package:
            if sum(item.file_size for item in package.infolist())>100*1024*1024:
                raise ValueError('Expanded portfolio exceeds 100 MiB.')
            for item in package.infolist():
                path=PurePosixPath(item.filename)
                if path.is_absolute() or any(p.startswith('.') or ':' in p or '\\' in p for p in path.parts):
                    raise ValueError('Unsafe archive member.')
                if item.is_dir():
                    continue
                output=stage.joinpath(*path.parts)
                output.parent.mkdir(parents=True, exist_ok=True)
                with package.open(item) as source, output.open('wb') as target:
                    shutil.copyfileobj(source,target)
        for required in ['index.html','projects/index.html','projects/outpost/index.html','lab/index.html']:
            if not (stage/required).is_file():
                raise ValueError('Incomplete portfolio build.')
        if '/works/' not in (stage/'index.html').read_text(encoding='utf-8'):
            raise ValueError('Build must use SITE_BASE=/works/.')
        previous=None
        if destination.exists():
            previous=destination.with_name(destination.name+'-previous-'+time.strftime('%Y%m%d-%H%M%S'))
            if previous.exists():
                raise ValueError('Previous-build destination exists; retry later.')
            destination.rename(previous)
        try:
            stage.rename(destination)
        except OSError:
            if previous is not None:
                previous.rename(destination)
            raise
    return digest.hexdigest(), total


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url',required=True)
    parser.add_argument('--sha256',required=True)
    parser.add_argument('--destination',type=Path,default=Path(__file__).resolve().parents[1]/'portfolio')
    args=parser.parse_args()
    sha,size=install(args.url,args.sha256,args.destination)
    print(f'Portfolio installed: {size} bytes; sha256={sha}')
