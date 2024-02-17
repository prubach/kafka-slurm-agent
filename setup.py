# -*- coding: utf-8 -*-
from skbuild import setup
from setuptools import find_packages

with open("README.md", "r") as fh:
    long_description = fh.read()

setup(
    name="kafka_slurm_agent",
    version='1.2.3',
    author="Paweł Rubach",
    author_email="pawel.rubach@gmail.com",
    description="The Kafka Slurm Agent is a distributed computing and stream processing engine "
                "that can be used to run python code across"
                "multiple SLURM managed HPC clusters or individual workstations."
                "It uses Kafka to asynchronously communicate with agents installed on clusters and workstations."
                "It contains a monitoring tool with a Web JSON API and a job submitter."
                "It is a pure Python implementation using faust stream processing",
    long_description=long_description,
    long_description_content_type="text/markdown",
    url="https://github.com/prubach/kafka-slurm-agent",
    packages=find_packages(),
    #package_data={
    data_files={
        "kafkaslurm_cfg.py__",
        "monitor_agent",
        "worker_agent",
        "cluster_agent"
    },
    entry_points={
        'console_scripts': ['kafka-slurm=kafka_slurm_agent.runner:run'],
    },
    install_requires=[
        'simple-slurm', 'kafka-python', 'psutil>=5.6.6', 'python-math', 'faust-streaming', 'werkzeug', 'wrapt-timeout-decorator'
    ],
    python_requires='>=3.6.0',
    classifiers=[
        "Programming Language :: Python :: 3",
        'Programming Language :: Python :: 3.6',
        'Programming Language :: Python :: 3.7',
        'Programming Language :: Python :: 3.8',
        'Programming Language :: Python :: 3.9',
        'Programming Language :: Python :: 3.10',
        'Programming Language :: Python :: 3.11',
        "License :: OSI Approved :: MIT License",
        "Operating System :: OS Independent"
    ],

)
