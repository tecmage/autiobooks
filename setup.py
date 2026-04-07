from setuptools import setup, find_packages

setup(
    name='autiobooks',
    version='1.6.0',
    packages=find_packages(),
    install_requires=[
        'pillow>=10.0.0',
        'tk>=0.1.0',
        'kokoro>=0.7.9,<0.8.0',
        'ebooklib>=0.18,<0.19',
        'soundfile>=0.13.1,<0.14.0',
        'pygame>=2.0.1,<3.0.0',
        'bs4>=0.0.2,<0.0.3',
        'lxml>=4.9.0',
    ],
    author='David Nesbitt',
    description='Automatically convert epubs to audiobooks',
    long_description=open('README.md').read(),
    long_description_content_type='text/markdown',
    url='https://github.com/plusuncold/autiobooks',
    classifiers=[
        'Programming Language :: Python :: 3',
        'Programming Language :: Python :: 3.10',
        'Programming Language :: Python :: 3.11',
        'Programming Language :: Python :: 3.12',
        'License :: OSI Approved :: MIT License',
        'Operating System :: OS Independent',
    ],
    python_requires='>=3.10,<3.13',
)
