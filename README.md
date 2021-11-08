# Kafka Slurm Agent

The Kafka Slurm Agent is a tool for submitting computing tasks to the Slurm queues on multiple clusters. 
It uses Kafka to asynchronously communicate with an agent installed on each cluster. 
It contains a monitoring tool and a job submitter.

## Installation.

Use the standard ``pip`` tool to install. The recommended way is to use a Python virtual environment:
``python3 -m venv venv``
``source venv/bin/activate``
``pip install kafka-slurm-agent``

## Using

In the folder in which you created the ``venv`` subfolder run the following command:
``kafka-slurm create .``
This will generate a configuration file ``kafkaslurm_cfg.py``, startup scripts and a module (``my_monitor_agent.py``) 
for adding your own implementation of the monitoring agent. This implementation may react to the incoming events
with computed jobs and handle them.

