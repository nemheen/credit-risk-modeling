FROM continuumio/miniconda3:22.11.1

# Set Python timezone
ENV TZ "Asia/Ulaanbaatar"

# Copy library scripts to execute
COPY .devcontainer/library-scripts/oracle-instant-client-debian.sh /tmp/library-scripts/

# [Optional] Uncomment this section install Oracle instant client
RUN bash /tmp/library-scripts/oracle-instant-client-debian.sh \
    && apt-get clean -y && rm -rf /var/lib/apt/lists/* && rm -rf /tmp/library-scripts

# Set working directory
WORKDIR /myapp

# Copy environment.yml to a temp location to update the environment.
COPY environment.yml requirements.txt ./
RUN /opt/conda/bin/conda env update -n base -f environment.yml

# Copy initialization script
COPY script.py /myapp
# Copy source code
COPY src/ /myapp/src

# Execute command
CMD ["python3", "script.py"]
