from setuptools import find_packages, setup

setup(
    name="beautybydaz_woocommerce_sync",
    version="1.0.0",
    description="Beauty By Daz ERPNext WooCommerce integration",
    author="Convergenix",
    packages=find_packages(),
    include_package_data=True,
    zip_safe=False,
    install_requires=["requests>=2.28"],
)
