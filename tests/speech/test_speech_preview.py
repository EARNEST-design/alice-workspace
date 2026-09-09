"""Execute speech preview clock/seek logic with a fake DOM and no devices."""

import json
import shutil
import subprocess
from importlib.resources import files

import pytest


def test_backward_seek_hides_channels_not_yet_in_sparse_expression():
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node is optional; required for the preview JS regression")
    payload = {
        "plan": {"segments": [{"text": "Hello."}]},
        "expression_mode": "supplied-proposal",
        "timeline": {
            "sample_count": 2000,
            "sample_rate": 1000,
            "spans": [{"start_sample": 0, "end_sample": 2000, "segment_index": 0}],
            "frames": [
                {
                    "sample_index": n,
                    "mouth_aperture": 0.5,
                    "vector": [0, 0, 0],
                    "intensity": 0,
                }
                for n in (0, 1000, 2000)
            ],
            "motion": [
                {
                    "targets": [
                        {"actuator_name": "mouth_open", "normalized_position": 0}
                    ]
                },
                {
                    "targets": [
                        {"actuator_name": "head_tilt", "normalized_position": 0.7}
                    ]
                },
                {
                    "targets": [
                        {"actuator_name": "head_tilt", "normalized_position": 0.1}
                    ]
                },
            ],
        },
    }
    template = files("alice.resources").joinpath("speech-preview.html").read_text()
    script = template.rsplit("<script>", 1)[1].split("</script>", 1)[0]
    harness = """
const assert=require('node:assert/strict');
class Element {
 constructor(){this.textContent='';this.currentTime=0;this.paused=false;this.hidden=false;}
 setAttribute(name,value){this[name]=value;}
 append(...children){}
}
const elements=new Map();
const document={
 getElementById(id){
  if(!elements.has(id))elements.set(id,new Element());return elements.get(id);
 },
 createElement(){return new Element();}
};
function requestAnimationFrame(){}
document.getElementById('speech-data').textContent=PAYLOAD;
""".replace("PAYLOAD", json.dumps(json.dumps(payload)))
    assertions = """
audio.currentTime=1.2;draw();
assert.equal(targetRows.get('head_tilt').value.textContent,'0.700');
assert.equal(targetRows.get('head_tilt').meter.hidden,false);
audio.currentTime=0.2;draw();
assert.equal(targetRows.get('head_tilt').meter.hidden,true);
assert.equal(targetRows.get('head_tilt').value.textContent,'—');
assert.equal(targetRows.get('mouth_open').value.textContent,'0.000');
audio.currentTime=1.2;draw();
assert.equal(targetRows.get('head_tilt').meter.hidden,false);
assert.equal(targetRows.get('head_tilt').value.textContent,'0.700');
"""
    result = subprocess.run(
        [node, "-e", harness + script + assertions], capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr
