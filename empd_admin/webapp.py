import os
import json
import io
import tornado.escape
import tornado.httpserver
import tornado.ioloop
import tornado.web
import tempfile
import github
import traceback

import empd_admin.repo_test as test
import empd_admin.parsers as parsers


# True if HEROKU env variable like true, True, yes, etc.
ONHEROKU = os.getenv('HEROKU', 'false').lower()[0] in 'ty'


class CommandHookHandler(tornado.web.RequestHandler):
    def post(self):
        headers = self.request.headers
        event = headers.get('X-GitHub-Event', None)

        if event == 'ping':
            self.write('pong')
        elif event == 'pull_request_review' or event == 'pull_request' \
                or event == 'pull_request_review_comment':
            # body = tornado.escape.json_decode(self.request.body)
            body = json.loads(self.request.body, strict=False)
            action = body["action"]
            repo_name = body['repository']['name']
            owner = body['repository']['owner']['login']
            # Only do anything if we are working with EMPD2
            if owner != 'EMPD2':
                return
            pr_repo = body['pull_request']['head']['repo']
            pr_owner = pr_repo['owner']['login']
            pr_repo = pr_repo['name']
            pr_branch = body['pull_request']['head']['ref']
            pr_num = body['pull_request']['number']
            comment = None
            if event == 'pull_request_review' and action != 'dismissed':
                comment = body['review']['body']
            elif event == 'pull_request' and action in ['opened', 'edited',
                                                        'reopened']:
                comment = body['pull_request']['body']
            elif (event == 'pull_request_review_comment' and
                  action != 'deleted'):
                comment = body['comment']['body']

            if comment:
                message = parsers.process_comment(
                    comment, pr_owner, pr_repo, pr_branch)
                if message:
                    test.comment_on_pr(
                        owner, repo_name, pr_num, message, force=True)

        elif event == 'issue_comment' or event == "issues":
            body = json.loads(self.request.body, strict=False)
            action = body["action"]
            repo_name = body['repository']['name']
            owner = body['repository']['owner']['login']
            issue_num = body['issue']['number']

            # Only do anything if we are working with conda-forge
            if owner != 'EMPD2':
                return
            pull_request = False
            if "pull_request" in body["issue"]:
                pull_request = True
            if pull_request and action != 'deleted':
                gh = github.Github(os.environ['GH_TOKEN'])
                repo_owner = gh.get_user(owner)
                remote_repo = repo_owner.get_repo(repo_name)
                pull = remote_repo.get_pull(issue_num)
                pr_repo = pull.raw_data['head']['repo']
                pr_owner = pr_repo['owner']['login']
                pr_repo = pr_repo['name']
                pr_branch = pull.raw_data['head']['ref']
                comment = body['comment']['body']
                message = parsers.process_comment(
                    comment, pr_owner, pr_repo, pr_branch, issue_num)
                if message:
                    test.comment_on_pr(
                        owner, repo_name, issue_num, message, force=True)

        else:
            print('Unhandled event "{}".'.format(event))
            self.set_status(404)
            self.write_error(404)


class TestHookHandler(tornado.web.RequestHandler):
    def post(self):
        headers = self.request.headers
        event = headers.get('X-GitHub-Event', None)

        if event == 'ping':
            self.write('pong')
        elif event == 'pull_request':
            body = tornado.escape.json_decode(self.request.body)
            repo_name = body['repository']['name']
            repo_url = body['repository']['clone_url']
            owner = body['repository']['owner']['login']
            pr_repo = body['pull_request']['head']['repo']
            pr_owner = pr_repo['owner']['login']
            pr_repo = pr_repo['name']
            pr_branch = body['pull_request']['head']['ref']
            pr_id = int(body['pull_request']['number'])
            is_open = body['pull_request']['state'] == 'open'

            # Only do anything if we are working with EMPD2, and an open PR.
            if is_open and owner == 'EMPD2':
                if body['sender']['login'] == 'EMPD-admin':
                    self.write('EMPD-admin pushes are skipped')
                    return
                self.write("testing PR %i from %s/%s" % (
                    pr_id, owner, repo_name))

                with tempfile.TemporaryDirectory('_empd') as tmp_dir:
                    test_info = test.download_pr(
                        owner, repo_name, pr_id, tmp_dir)

                    # display information on the PR to the user
                    if not test_info:
                        test_info = test.pr_info(tmp_dir, pr_owner, pr_repo,
                                                 pr_branch)
                        if test_info:
                            msg = test.comment_on_pr(
                                owner, repo_name, pr_id, test_info['message'],
                                onlyif='any',
                                force=(test_info['status'] == 'failure'))
                            test.set_pr_status(owner, repo_name, test_info,
                                               target_url=msg.html_url)

                        # run the tests
                        test_info = test.full_repo_test(tmp_dir)

                if test_info and test_info['status'] != 'skipped':
                    msg = test.comment_on_pr(
                        owner, repo_name, pr_id, test_info['message'])
                    test.set_pr_status(owner, repo_name, test_info,
                                       target_url=msg.html_url)
        else:
            print('Unhandled event "{}".'.format(event))
            self.set_status(404)
            self.write_error(404)


class ViewerHookHandler(tornado.web.RequestHandler):

    def set_default_headers(self):
        self.set_header("Access-Control-Allow-Origin", "*")
        self.set_header("Access-Control-Allow-Headers", "x-requested-with")
        self.set_header('Access-Control-Allow-Methods', 'POST, GET, OPTIONS')

    def post(self):
        body = tornado.escape.json_decode(self.request.body)
        try:
            repo = body['repo']
            branch = body['branch']
            meta = body['meta']

            metadata = body['metadata']

            submitter_first = body['submitter_firstname']
            submitter_last = body['submitter_lastname']
            submitter_mail = body['submitter_mail']
            submitter_gh = body['submitter_username']
        except KeyError:
            s = io.StringIO()
            traceback.print_exc(file=s)
            print('Unhandled request!\n\n' + s.getvalue())
            self.set_status(404)
            self.write_error(404)
        else:

            if ONHEROKU:
                import requests
                recaptcha_token = body['token']
                response = requests.post(
                    'https://www.google.com/recaptcha/api/siteverify',
                    data={'response': recaptcha_token,
                          'secret': os.environ['RECAPTCHASECRET']})
                validation = json.loads(response.text)
                print(validation)
                if (not validation['success'] or validation['score'] < 0.5
                        or validation['action'] != 'submit_data'):
                    self.write(
                        "Failed recaptcha validation: %s " % validation)
                    self.set_status(401)
                    self.write_error(401)
                    return

            from empd_admin.viewer_responses import handle_viewer_request
            success, msg = handle_viewer_request(
                metadata, (submitter_first + ' ' + submitter_last).strip(),
                repo, branch, meta, submitter_gh)
            self.write(msg + ' ')
            if not success:
                self.set_status(500)
                self.write_error(500)
            print(success, msg)
            if ONHEROKU:
                # send a mail to the sender
                import sendgrid
                import os
                from sendgrid.helpers.mail import (
                    Email, HtmlContent, Mail, Personalization)

                sg = sendgrid.SendGridAPIClient(
                    apikey=os.environ.get('SENDGRID_API_KEY'))
                from_email = Email("noreply@empd.com")
                status = "" if success else "FAILED: "
                subject = status + f"Data contribution to {repo}:{branch}"
                to_mail = Email(submitter_mail)
                content = HtmlContent(msg)
                mail = Mail(from_email, to_mail, subject,
                            html_content=content)
                response = sg.client.mail.send.post(request_body=mail.get())
                print(response.status_code)
                print(response.body)
                print(response.headers)


def create_webapp():
    application = tornado.web.Application([
        (r"/empd-data/hook", TestHookHandler),
        (r"/empd-admin-command/hook", CommandHookHandler),
        (r"/empd-viewer/hook", ViewerHookHandler),
    ])
    return application


def main():
    application = create_webapp()
    http_server = tornado.httpserver.HTTPServer(application, xheaders=True)
    port = int(os.environ.get("PORT", 5000))

    # https://devcenter.heroku.com/articles/optimizing-dyno-usage#python
    n_processes = int(os.environ.get("WEB_CONCURRENCY", 1))

    if n_processes != 1:
        # http://www.tornadoweb.org/en/stable/guide/running.html#processes-and-ports
        http_server.bind(port)
        http_server.start(n_processes)
    else:
        http_server.listen(port)
    tornado.ioloop.IOLoop.instance().start()


if __name__ == "__main__":
    main()
