from kafka_slurm_agent.command import Command

def test_cmd():
    #cmd = Command('kafka-slurm create .')
    cmd = Command('uname')
    cmd.run(10)
    res = cmd.getOut()
    assert res == 'Linux\n'
