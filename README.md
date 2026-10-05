# stepdoc

Turn test code into test documentation: a **procedure** with symbolic values
(`dev.map.dac.level = <level>`, `GET /users/<user_id>`) and an **executed
report** with the concrete values of one run, from the same `with step(...)`
blocks and the actions your system under test emits.

Status: early spike. See [SPIKE.md](SPIKE.md) for what was prototyped and measured.

```bash
pip install -e ".[dev]"
pytest
python examples/spike_demo.py      # prints a procedure and a report for both examples
python bench/bench_events.py       # cost per recorded event
```
