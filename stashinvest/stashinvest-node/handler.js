'use strict';

//set environment properties
const wordpress_url = process.env.wordpress_url;
const zendesk_url = process.env.zendesk_url; //stashinvesthelp
const zendesk_username = process.env.zendesk_username;
const zendesk_api_token = process.env.zendesk_api_token; // FU1pWKPP4yMEVOrtUCoCnpwLQwFI3hLJQgV8ojb4
//const wordpress_username = '';
//const wordpress_password = '';
//const error_email_address = '';
const default_category = 11;
const wordpress_per_page = '20';
const retryInterval = 30000;
var cache = {};

 
// add an 'item' to the cached list for a given 'key'
function addItemToCache(key,item) {
  if (key in cache && cache[key] && cache[key].length) {
    cache[key].push(item);
  } else {
    cache[key] = item;
  }
}
 
// filter an array down to unique values
function onlyUnique(val, index, self) { 
  return self.indexOf(val) === index;
}
 
// error logging
function reportError(details) {
  var formatted_error = arguments.callee.name + ':\n' + JSON.stringify(details);
  console.error(formatted_error); // log the error
  //MailApp.sendEmail(email_address, 'Google Script Error: ' + arguments.callee.name, details); // email the error
}
 
 
/* --- Start Wordpress Functions --- */
 
// retrieve Wordpress pages
function getWordpressPages(page,dataList) {
  return new Promise(function(resolve,reject) {
    if(!page){page=1;}
    if(!dataList){dataList=[];}
    var url = wordpress_url + '/ask-api'; //?per_page=100&orderby=date&order=desc&page=' + page.toString();
    var request = require("request-promise");
    var options = { method: 'GET',
      url: url,
      qs: { page: page.toString(), per_page: wordpress_per_page },
      headers: 
       { 'max-age': '0',
         'cache-control': 'no-cache' } };
 
    request(options, function (error, response, body) {
      if (error) throw new Error(error);
      var data = JSON.parse(body);
      dataList = dataList.concat(data);
      var totalPages = response.headers['x-wp-totalpages'];
      //resolve(dataList); // FOR TESTING
      if (totalPages && (page < totalPages)) {
        getWordpressPages(page+1,dataList).then(function(dL){
          resolve(dL);
        });
      } else {
        resolve(dataList);
      }
    });
  });
}
 
// parse a Wordpress page into Zendesk article format 
function parseWordpress(page) {
   
  // only parse if there is content
  if (page) { 
 
    // page title is required
    if (!page.title || !page.title.rendered) { 
      throw 'page title missing';
    } 
     
    // page content is required
    else if (!page.content || !page.content.rendered) {
      throw 'page content missing';
    } 
     
    else {
      try {
        return {
          "title": page['title']['rendered'],
          "body": page['content']['rendered'],
          "updated_at": page['modified_gmt'] + 'Z'
        }
      } catch (err) {
        reportError(err);
      }
    }
  }
}
 
// retrieve the last category id from a page
function lastCategoryId(page) {
  if (page.categories.length) {
    return page.categories[page.categories.length-1];
  } else {
    return default_category;
  }
}
 
// retrieve category name from worpdress id
function getCategoryName(id) {
  return new Promise(function(resolve,reject) {
    // check the cache
    var cached = cache[id];
    if (cached != null) {
      resolve(cached);
    } else {
      var request = require("request-promise");
      var url = wordpress_url + '/categories/' + id.toString();
      var options = { method: 'GET',
        url: url };
      request(options, function (error, response, body) {
        if (error) throw new Error(error);
        var data = JSON.parse(body);
        var category_name = data.name.replace('&amp;','&');
        addItemToCache(id,category_name);
        resolve(category_name);
      });
    }
  });
}
 
/* --- End Wordpress Functions --- */
 
 
/* --- Start Zendesk Functions --- */
 
// collect Zendesk objects defined by params.obj_type
function fetchZendeskObjects(params) {
  return new Promise(function(resolve,reject) {
    var options = {};
    var obj_type = params.obj_type;
    var zendesk_auth = "Basic " + Buffer.from(zendesk_username + ":" + zendesk_api_token).toString('base64');
    var url = params.next_page || (zendesk_url + '/help_center/'+obj_type+'.json');
    if (!params[obj_type]) { params[obj_type] = []; }
     
    var request = require("request-promise");
    var options = { method: 'GET',
      url: url,
      headers: 
       { 'cache-control': 'no-cache',
         authorization: zendesk_auth } };
 
    request(options, function (error, response, body) {
      if (error) throw new Error(error);
      var data = JSON.parse(body);
      params[obj_type].push.apply(params[obj_type],data[obj_type]);
      // pagination uses recursion until next_page is null
      if (data.next_page) {
        params.next_page = data.next_page;
        fetchZendeskObjects(params).then(function(prms){
          resolve(prms);
        });
      } else {
        resolve(params[obj_type]);
      }
    });
  });
}
 
// make a post request to Zendesk's API
function zendeskPost(endpoint_url,payload_data) {
  return new Promise(function(resolve,reject) {
    var url = zendesk_url + endpoint_url;
    var zendesk_auth = "Basic " + Buffer.from(zendesk_username + ":" + zendesk_api_token).toString('base64');
    var payload = JSON.stringify(payload_data);
    var request = require("request-promise");
    var options = { method: 'POST',
      url: url,
      headers: 
       { 'cache-control': 'no-cache',
         authorization: zendesk_auth,
         'content-type': 'application/json' },
      body: payload };
    return request(options, function (error, response, body) {
    	
    	if (error) {
        console.log(error);
        //throw new Error(error);
        resolve();
      } else {
    	    if(response.statusCode == 429) {
        		setTimeOut(function(){
        			resolve(zendeskPost(endpoint_url,payload_data))
        		}, retryInterval)
        	}
    	    console.log(response);
        var data = JSON.parse(body);
        resolve(data);
      }
    })
    .catch(function(err){
      console.log('post failed:' + endpoint_url + ':' + JSON.stringify(payload_data));
      //console.log(err.body);
    });
  });
}
 
// Create a category and nested section of the same name
function createZendeskCategoryAndSection(category_name) {
  return new Promise(function(resolve,reject) {
    if (category_name) {
      getZendeskCategories().then(function(cats){
        var results = cats.filter(function (el) {
          return el.name == category_name;
        });
        if (results.length > 0) {
          var category_id = results[0].id;
          sectionCreateOrUpdate(category_id,category_name).then(function(res){
            resolve(res);
          });
        } else {
          createZendeskCategory(category_name).then(function(new_category){
            var new_category_id = new_category.id;
            sectionCreateOrUpdate(new_category_id,category_name).then(function(res){
              resolve(res);
            });
          });
        }
      });
    }
  });
}
 
// identify the correct section id
function sectionCreateOrUpdate(new_category_id,section_name) {
  return new Promise(function(resolve,reject){
    if (!new_category_id || !section_name) {
      reportError({'function':'sectionCreateOrUpdate','message':'missing category id or section name','new_category_id':new_category_id,'section_name':section_name});
    } else {
      getZendeskSections().then(function(sections){
        var results = sections.filter(function (el) {
          return ((el.name == section_name) && (new_category_id == el.category_id));
        });
        if (results.length > 0) {
          var section_id = results[0].id;
          resolve(section_id);
        } else {
          createZendeskSection(new_category_id,section_name).then(function(newSection){
            var new_section_id = newSection.id;
            resolve(new_section_id);
          });
        }
      });  
    }
  });
}
 
// collect all zendesk categories
function getZendeskCategories() {
  return new Promise(function(resolve,reject) {
    var cached = cache.categories;
    if (cached != null) {
      resolve(cached);
    }
    var params = {'obj_type':'categories'};
    fetchZendeskObjects(params).then(function(categories){
      var category_cache = categories.map(function(c){return {'name':c.name,'id':c.id}});
      addItemToCache('categories',category_cache);
      resolve(categories);
    });
  });
}
 
// collect all zendesk sections
function getZendeskSections() {
   
  return new Promise(function(resolve,reject) {
    var cached = cache.sections;
    if (cached != null) {
      resolve(cached);
    }
    var params = {'obj_type':'sections'};
    fetchZendeskObjects(params).then(function(sections) {
      var section_cache = sections.map(function(s){return {'category_id':s.category_id,'name':s.name,'id':s.id}});
      addItemToCache('sections',section_cache);
      resolve(sections);
    });
  });
}
 
// get all zendesk articles
function getZendeskArticles() {

  return new Promise(function(resolve,reject) {
    var cached = cache.articles;
    if (cached != null) {
      resolve(cached);
    }
    var params = {'obj_type':'articles'};
    fetchZendeskObjects(params).then(function(articles) {
      var article_cache = articles.map(function(a){return {'section_id':a.section_id,'title':a.title,'id':a.id,'updated_at':a.updated_at}});
      addItemToCache('articles',article_cache);
      resolve(articles);
    });
  });
}
 
// function: post a category to Zendesk's API
function createZendeskCategory(category_name) {
  return new Promise(function(resolve,reject) {
    var endpoint = '/help_center/categories.json';
    var data = {'category':{'name':category_name}};
    try {
      zendeskPost(endpoint,data).then(function(resp){
        addItemToCache('categories',resp.category);
        resolve(resp.category);
      });
    } catch(err) {
      console.error('category creation failed');
      console.error(err);
    }  
  });
}
 
// function: post a section to Zendesk's API
function createZendeskSection(category_id,section_name) {
  return new Promise(function(resolve,reject) {
    var endpoint = '/help_center/categories/' + category_id.toString() + '/sections.json';
    var data = {'section':{'name':section_name}};
    try {
      zendeskPost(endpoint,data).then(function(resp){
        addItemToCache('sections',resp.section);
        resolve(resp.section);
      });
    } catch(err) {
      console.error('section creation failed');
      console.error(err);
    }
  });
}
 
// post an article to Zendesk's API
function createZendeskArticle(section_id,article_data) {
  return new Promise(function(resolve,reject) {
    if (!article_data || !section_id || !article_data.body){
      reject('no data');
    }
    var endpoint = '/help_center/sections/'+section_id.toString()+'/articles.json';
    var data = {'article':article_data};
    try {
      zendeskPost(endpoint,data).then(function(resp){
        addItemToCache('articles',resp.article);
        resolve(resp.article);
      });
    }
    catch(err) {
      console.error('article creation failed');
      //console.error(err.body);
    }
  });
}
 
// put an article to Zendesk's API
function updateArticle(article_id,article_data) {
  return new Promise(function(resolve,reject) {
    var endpoint = '/api/v2/help_center/articles/' + article_id.toString() + '/translations/en-us.json';
    var data = {'translation':article_data};
    zendeskPost(endpoint,data).then(function(resp){
      resolve(resp.translation);
    });
  });
}
 
// find a matching article if it exists
async function searchArticles(articles,section_id,article_data) {
  return new Promise(function(resolve,reject) {
    for (var i in articles) {
      var article = articles[i];
      if ((parseInt(section_id) === parseInt(article.section_id)) && (article.title.toLowerCase() === article_data.title.toLowerCase())) {
        resolve(article);
      }
    }
    resolve();
  });
}
 
// identify the correct article id
function articleCreateOrUpdate(section_id,article_data,articles) {
  return new Promise(function(resolve,reject) {
    // search by name / section id
    searchArticles(articles,section_id,article_data).then(function(article_match){
      // if a match is found
      if (article_match) {
        // and it is out of date
        if (article_match.updated_at < article_data.updated_at) {
          // update it
          updateArticle(article_match.id,article_data).then(function(updated_article) {
            if (updated_article) {
              // todo: log the update
              //return updated_article.id;
              resolve();
            } else {
              reportError({'function':'articleCreateOrUpdate','message':'cannot update article','name':article_data.title});
            }
          });
        } else {
          resolve();
        }
      } else {
        // create the article if it doesn't exist
        createZendeskArticle(section_id,article_data).then(
          function(new_article) {
            if (!new_article) {
              reportError({'function':'articleCreateOrUpdate','message':'cannot migrate page','name':article_data.title});
              resolve();
            } else {
              resolve();
              // todo: log the update
            }
          }, 
          function(err) {
            reportError({'function':'articleCreateOrUpdate','message':'rejected','name':article_data.title});
            console.log(err);
          })
          .catch(function(err){
          console.log(err);
        });
 
      }
    });
  });
}
 
/* --- End Zendesk functions --- */
 
 
/* --- Begin Core Process functions --- */
 
function processPage(page,wp_category_id,section_id,articles) {
  return new Promise(function(resolve,reject) {
    var page_category_id = lastCategoryId(page);
    if (wp_category_id == page_category_id) {
      var article_data = null;
      try {
        article_data = parseWordpress(page);
      }
      catch(err) {
        reportError({'function':'updateArticles','message':err,'value':page.guid.rendered});
      }
      if (article_data) {
        articleCreateOrUpdate(section_id,article_data,articles).then(function(resp){
          resolve();
        });
      }
    }
  });
}
 
function processPages(pages,wp_category_id,section_id,articles){
  return new Promise(function(resolve,reject) {
    var promises = [];
    pages.forEach(function(page) {
      promises.push(processPage(page,wp_category_id,section_id,articles));
    });
    Promise.all(promises).then(function(vals){
      resolve();
    });
  });
}
 
function main(callback) {     
	// retrieve all word press pages
    getWordpressPages().then(function(pages) {
	    getZendeskArticles().then(function(articles){
	      // identify the category ids
	      var category_ids = pages.map(lastCategoryId).filter(onlyUnique);
	 
	      category_ids.forEach(function(wp_category_id) {
	        getCategoryName(wp_category_id)
	           
	          // create categories and sections
	          .then(createZendeskCategoryAndSection) 
	          .then(function(section_id){
	        	    // create articles
	            processPages(pages,wp_category_id,section_id,articles).then(function(callback){
	            		console.log('done');
	            });
	          });
	      });
	    });
  }); 
}

module.exports.wordpress_to_zendesk = (event, context, callback) => {
	console.log("Calling main function...")
	main(callback);
	console.log("Complete calling main function...")	

  // Use this code if you don't use the http event with the LAMBDA-PROXY integration
  // return { message: 'Go Serverless v1.0! Your function executed successfully!', event };
};