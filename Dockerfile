FROM python:3.10.7-slim

RUN apt update && apt upgrade -y && apt install -y git nano subversion

ENV LC_ALL=C.UTF-8

WORKDIR /app
ADD . /app

RUN pip install -r requirements.txt

# Set up local testing svn remote
RUN svnadmin create /svn_remote
RUN mkdir -p /tmp/svn
RUN svn co file:///svn_remote /tmp/svn
WORKDIR /tmp/svn
RUN touch test_file.txt
RUN svn add test_file.txt
RUN svn commit -m "Initial commit"
RUN svn update
WORKDIR /app

# Set up local testing git remote
RUN git config --global user.email "test@test.com"
RUN git config --global user.name "Docker Test User"
RUN git init --bare /remote.git
RUN git clone /remote.git /tmp/git
RUN mkdir /tmp/git/masterfiles
RUN touch /tmp/git/masterfiles/test_file.txt
RUN git -C /tmp/git add masterfiles/test_file.txt && git -C /tmp/git commit -m "Initial commit" && git -C /tmp/git push
# Remote must be a bare repository with no working copy in order for commits to be pushed to it safely

# Local working copies left in /tmp for testing

ENTRYPOINT ["python", "main.py"]

CMD ["-h"]
