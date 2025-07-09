# Kafka Slurm Agent

The Kafka Slurm Agent is a distributed computing and stream processing engine 
that can be used to run python code across multiple **SLURM** managed HPC clusters or individual workstations.
It uses Kafka (3.x) to asynchronously communicate with agents installed on clusters and workstations.
It contains a monitoring tool with a Web JSON API and a job submitter.
It is a pure Python implementation using faust stream processing

## Installation.

Use the standard ``pip`` tool to install. The recommended way is to use a Python virtual environment:

``python3 -m venv venv``

``source venv/bin/activate``

``pip install kafka-slurm-agent``

## Quick User Guide

### Create a new project

In the folder in which you created the ``venv`` subfolder run the following command:

``kafka-slurm create-project --folder .``

This will generate the following files:
   1. ``kafkaslurm_cfg.py`` - the configuration file
   2. Startup scripts for **worker-agent**, **cluster-agent** and **monitor-agent**
   3. An example file to run your code (``run.py``)
   4. The job submitter example (``submitter.py``)
   5. The class that can be optionally used to override the existing implementation of the **worker-agent** (``my_worker_agent.py``)

### Configuration
Please adjust the config file.
1. Modify the configuration of the connection to **Apache Kafka**. The default one assumes that kafka is running on *localhost* and default port (*9092*) and doesn't use authentication or SSL.
     In the comments you will find parameters necessary to connect to **Kafka** configured using SASL and plaintext password. If you use this type of connection please uncomment also the line that starts with:
``# KAFKA_FAUST_BROKER_CREDENTIALS``
2. Make sure that ``PREFIX`` points to the location of your project
3. Change the names of topics used for your project to avoid any conflict with projects sharing the same kafka instance.
4. If you want to use a SLURM cluster please change the job ``CLUSTER_JOB_NAME_SUFFIX = '_KSA'`` to avoid conflicts with other projects running on your slurm cluster. The jobs managed by **cluster-agent** will be named *"JOBID_SUFFIX"* where the JOBID is the identifier that you assign when submitting a job and SUFFIX is handled by this configuration parameter.

For a full list of configuration parameters refer to the documentation.

### Creating topics on Kafka with appropriate partitions

Use the built-in command ``kafka-slurm`` to create topics. You should set the ``--new-topic-partitions`` paramter to at least the number of planned clusters and workstations that will be used simultaneously.
For example:

``kafka-slurm --new-topic-partitions 4 topics-create``

### Implement your code

An example of a script that you can implement is generated in ``run.py``

## Submitting jobs

Once this is ready, you can test your project locally:
1. Open a new terminal and start the **worker-agent** (``./start_worker_agent``)
2. Open a new terminal and start the **monitor-agent** (``./start_monitor_agent``)
3. Submit new jobs using the ``submitter.py``

You can monitor the execution by opening http://localhost:6067/mon/stats/ on the host on which you've started the **monitor-agent**.

## Kafka

For development and testing purposes, you can use Kafka in Docker:

``docker run -d -p 9092:9092 --name broker apache/kafka:3.9.1``

## Demo project

You can download and directly run a demonstration project: https://github.com/ilbsm/ksa_demo

## Limitations

Due to the underlying libraries (faust-streaming and it's dependency: aiokafka) currently Kafka 4.0 is not supported.

## Cite

If you use Kafka Slurm Agent in your research, please cite:

[Applying Large-Scale Distributed Computing to Structural Bioinformatics -- Bridging Legacy HPC Clusters With Big Data Technologies Using kafka-slurm-agent](https://arxiv.org/abs/2503.14806)

You can also read more about the motivation and applications of this tool.  