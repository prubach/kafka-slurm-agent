# -*- coding: utf-8 -*-
from skbuild import setup
from setuptools import find_packages

with open("README.md", "r") as fh:
    long_description = fh.read()

setup(
    name="kafka_slurm_agent",
    version='0.1.1',
    author="Paweł Rubach",
    author_email="pawel.rubach@gmail.com",
    description="The Kafka Slurm Agent is a tool for submitting computing tasks to the Slurm queues on multiple "
                "clusters. It uses Kafka to asynchronously communicate with an agent installed on the cluster."
                "It contains a monitoring tool"
                "and This package provides a set of tools for accessing protein databases and manipulating PDB/CIF files.",
    long_description=long_description,
    long_description_content_type="text/markdown",
    url="https://github.com/prubach/kafka-slurm-agent",
    packages=find_packages(),
    #package_data={
    data_files={
        "kafkaslurm_cfg.py__"
    },
    entry_points={
        'console_scripts': ['kafka-slurm=kafka_slurm_agent.runner:run'],
    },
    install_requires=[
        'simple-slurm', 'kafka-python', 'psutil>=5.6.6', 'python-math', 'faust-streaming', 'werkzeug'
    ],
    python_requires='>=3.6.0',
    classifiers=[
        "Programming Language :: Python :: 3",
        'Programming Language :: Python :: 3.6',
        'Programming Language :: Python :: 3.7',
        'Programming Language :: Python :: 3.8',
        'Programming Language :: Python :: 3.9',
        'Programming Language :: Python :: 3.10',
        "License :: OSI Approved :: MIT License",
        "Operating System :: OS Independent"
    ],

)
