pipeline {
    agent any

    stages {
        stage('Checkout Code') {
            steps {
                // Pull the latest code (including Jinja2 templates) from the repository
                checkout scm
            }
        }

        stage('Install J2Lint') {
            steps {
                // Install J2Lint if it's not already installed
                sh 'pip install --user j2lint'
            }
        }

        stage('Lint Jinja2 Templates') {
            steps {
                // Run J2Lint on all Jinja2 template files in the directory
                sh '''
                export PATH=$PATH:/home/student/.local/bin && j2lint template-generator/templates/*.j2
                '''
            }
        }

        stage('Verify Generated Config Exists') {
            steps {
                script {
                    // Check if there is at least one file in the generated-configs directory
                    def fileExists = sh(script: "ls /home/student/git/csci5840/template-generator/generated-configs/*.yaml 2>/dev/null || echo 'not found'", returnStdout: true).trim()

                    if (fileExists == 'not found') {
                        error("No configuration file found in template-generator/generated-configs directory.")
                    } else {
                        echo "Configuration file exists: ${fileExists}"
                    }
                }
            }
        }

        stage('Ping Test') {
            steps {
                script {
                    // Identify the latest YAML file in the generated-configs directory
                    def yamlFile = sh(script: "ls -t /home/student/git/csci5840/template-generator/generated-configs/*.yaml | head -n 1", returnStdout: true).trim()
                    
                    // Extract the device name by splitting the filename (e.g., "r3_core.yaml" -> "r3")
                    def deviceName = yamlFile.split('/').last().split('_')[0]

                    if (deviceName) {
                        // Run ping command and check if it succeeds
                        def result = sh(script: "ping -c 4 ${deviceName}", returnStatus: true)
                        if (result != 0) {
                            error("Ping test failed for device: ${deviceName}")
                        } else {
                            echo "Ping test successful for device: ${deviceName}"
                        }
                    } else {
                        error("Device name could not be determined from the file name.")
                    }
                }
            }
        }
    }

    post {
        success {
            echo 'Jenkins Job successful. No errors found!'
        }
        failure {
            echo 'Jenkins Job failed. Please check the errors!'
        }
    }
}
