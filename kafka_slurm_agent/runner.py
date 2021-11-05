import argparse
from kafka_slurm_agent.command import Command


def run():
    parser = argparse.ArgumentParser(prog="kafka-slurm", formatter_class=argparse.RawDescriptionHelpFormatter,
                                         description="kafka-slurm agent")
    parser.add_argument('start', action="action", help="Action to perform")
    parser.add_argument('script', action="script", help="Script to run. For builtin agents specify cluster_agent or monitor_agent")
    args = vars(parser.parse_args())
    if args['script'] in ['cluster_agent', 'monitor_agent']:
       script = 'kafka_slurm_agent.' + args['script']
    else:
        script = args['script']
    cmd = Command('faut -A ' + script + ' -l info worker')
    cmd.run()
