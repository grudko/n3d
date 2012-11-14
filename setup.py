#!/usr/bin/env python
from distutils.core import setup

setup(
    name='n3d',
    version='0.1',
    description='N3 Deployment Tool',
    author='Anton Grudko',
    author_email='grudko@gmail.com',
    url='http://develop.netrika.ru/n3d',
    platforms=('Any',),
    py_modules=['n3d'],
    install_requires=['pexpect', 'jinja2', 'termcolor'],
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
                   'Topic :: Software Development :: Build Tools'
                   ]
)
