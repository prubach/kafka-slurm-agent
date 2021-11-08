
from kafka_slurm_agent.command import Command


def test_runner():
    cmd = Command('kafka-slurm create .')
    cmd.run(10)
    res = cmd.getOut()
    assert res == ''
