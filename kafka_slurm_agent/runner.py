import argparse
import os
import shutil

from kafka_slurm_agent.command import Command

CONFIG_FILE = 'kafkaslurm_cfg.py__'

SCRIPTS = {
    'start_cluster_agent': '#!/bin/bash\nfaust -A kafka_slurm_agent.cluster_agent -l info worker\n',
    'my_monitor_agent.py': "from kafka_slurm_agent.monitor_agent import app, job_status, done_topic\n\n"
                        "#TODO Put your monitor agent code here\n\n\n"
                        "@app.agent(done_topic)\n"
                        "async def process_done(stream):\n"
                        "\tasync for msg in stream.events():\n"
                        "\t\tprint('Got {}: {}'.format(msg.key, msg.value))\n",
    'my_cluster_agent.py': "from kafka_slurm_agent.kafka_modules import ClusterAgent\n\n"
                           "class MyClusterAgent(ClusterAgent):\n"
                           "\tdef __init__(self):\n"
                           "\t\tsuper().__init__()\n"
                           "\t\tself.script_name = 'run.py'\n"
                           "\t\tself.job_name_suffix = '_MYJOBS'\n\n"
                           "\tdef get_job_name(self, input_job_id):\n"
                           "\t\treturn str(input_job_id) + self.job_name_suffix\n",
    'start_monitor_agent': '#!/bin/bash\nfaust -A my_monitor_agent -l info worker -p 6067\n'
}


class StartAction(argparse.Action):
    def __init__(self, option_strings, dest, nargs=None, **kwargs):
        if nargs is not None:
            raise ValueError("nargs not allowed")
        super().__init__(option_strings, dest, **kwargs)

    def __call__(self, parser, namespace, values, option_string=None):
        print('%r %r %r' % (namespace, values, option_string))
        setattr(namespace, self.dest, values)
        if values in ['cluster_agent', 'monitor_agent']:
            script = 'kafka_slurm_agent.' + values
        else:
            script = values
        cmd = Command('faust -A ' + script + ' -l info worker')
        cmd.run(10000)


class GenerateAction(argparse.Action):
    def __init__(self, option_strings, dest, nargs=None, **kwargs):
        if nargs is not None:
            raise ValueError("nargs not allowed")
        super().__init__(option_strings, dest, **kwargs)

    def __call__(self, parser, namespace, values, option_string=None):
        setattr(namespace, self.dest, values)
        folder = os.path.join(os.getcwd(), values)
        rootpath = os.path.abspath(os.path.dirname(__file__))
        while not os.path.isfile(os.path.join(rootpath, CONFIG_FILE)) and rootpath != os.path.abspath(os.sep):
            rootpath = os.path.abspath(os.path.dirname(rootpath))
        shutil.copy(os.path.join(rootpath, CONFIG_FILE), os.path.join(folder, CONFIG_FILE.replace('py__', 'py')))
        with open(os.path.join(folder, CONFIG_FILE.replace('py__', 'py')), 'a') as file_out:
            file_out.write("PREFIX = '" + os.path.abspath(folder) + "'\n")
            file_out.write("LOGS_DIR = PREFIX + '/logs'\n")
        for script, content in SCRIPTS.items():
            with open(os.path.join(folder, script), 'w') as file_out:
                file_out.write(content)
            if script.startswith('start'):
                os.chmod(script, 0o755)




def run():
    parser = argparse.ArgumentParser(prog="kafka-slurm", formatter_class=argparse.RawDescriptionHelpFormatter,
                                         description="kafka-slurm agent")
    parser.add_argument('create', help="Action to perform")
    #parser.add_argument('script', action=StartAction, help="Script to run. For builtin agents specify cluster_agent or monitor_agent")
    parser.add_argument('folder', action=GenerateAction, default='.',
                        help="Folder in which to create the agents home folder for the configuration file and startup scripts. By default local folder")
    args = vars(parser.parse_args())
