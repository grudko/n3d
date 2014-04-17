#!/usr/bin/env python
from setuptools import setup

setup(
    name='n3d',
    version='0.3.3',
    description='Utility for step-by-step application deployment',
    author='Anton Grudko',
    author_email='grudko@gmail.com',
    url="http://github.com/grudko/n3d",
    py_modules=['n3d'],
    install_requires=['pexpect', 'termcolor'],
    entry_points={
        'console_scripts': [
            'n3d = n3d:main',
        ]
    },
    classifiers = ['Development Status :: 4 - Beta',
                   'Environment :: Console',
                   'Intended Audience :: Developers',
                   'Intended Audience :: System Administrators',
                   'License :: OSI Approved :: GNU Lesser General Public License v3 (LGPLv3)',
                   'Operating System :: POSIX',
                   'Programming Language :: Python',
                   'Topic :: Software Development :: Build Tools',
                   ]
)
