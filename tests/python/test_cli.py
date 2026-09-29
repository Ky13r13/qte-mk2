import json
from pathlib import Path
import subprocess
import sys
import qte.cli

ROOT=Path(__file__).resolve().parents[2]

def test_command_runs_and_preserves_prior_output(tmp_path):
    output=tmp_path/'research'
    command=[sys.executable,'-m','qte','run','--config',str(ROOT/'examples/research-run.json'),'--output',str(output)]
    first=subprocess.run(command,capture_output=True,text=True)
    assert first.returncode==0,first.stderr
    report=json.loads((output/'report.json').read_text())
    assert report['fill_count']==2
    assert report['metrics']['annualized_volatility']['value'] is not None
    manifest=json.loads((output/'manifest.json').read_text())
    assert manifest['strategy']['slow_period']==3
    assert manifest['source_identity'].startswith('sha256:')
    assert (output/'fills.csv').read_text().count('\n')==3
    assert subprocess.run(command,capture_output=True).returncode==2

def test_unknown_configuration_fails_without_creating_output(tmp_path):
    path=tmp_path/'bad.json'; path.write_text('{"unknown":true}')
    out=tmp_path/'out'
    assert qte.cli.main(['run','--config',str(path),'--output',str(out)])==2
    assert not out.exists()
