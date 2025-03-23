import setuptools

with open("VERSION", 'r') as f:
    version = f.read().strip()

with open("README.md", 'r') as f:
    long_description = f.read()

setuptools.setup(
   name='streamdeck',
   version=version,
   description='Library to control Elgato StreamDeck devices.',
   author='Core447',
   author_email='core447@proton.me',
   url='https://github.com/StreamController/sc-python-elgato-streamdeck',
   package_dir={'': 'src'},
   packages=setuptools.find_packages(where='src'),
   install_requires=[],
   license="MIT",
   long_description=long_description,
   long_description_content_type="text/markdown",
   include_package_data=True,
   python_requires='>=3.8',
)
